import errno
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'guest/devices'))
import wifi_bridge as bridge


def capabilities(device, command, buffer):
    assert command == bridge.GET_CAPABILITIES
    struct.pack_into('=I', buffer, 0, bridge.CAP_REQUEST_POLL)


class WiFiRequestTests(unittest.TestCase):
    def test_legacy_module_is_rate_limited_but_other_errors_are_not_hidden(self):
        with patch.object(bridge.fcntl, 'ioctl', side_effect=OSError(errno.ENOTTY, 'old module')):
            wait = bridge.RequestWait(123)
        self.assertIsNone(wait.poller)
        with patch.object(bridge.time, 'sleep') as sleep:
            wait.wait(None); wait.wait(5); wait.wait(0.02)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.1, 0.1, 0.02])
        with patch.object(bridge.fcntl, 'ioctl', side_effect=PermissionError(errno.EPERM, 'denied')):
            with self.assertRaises(PermissionError): bridge.RequestWait(123)
        def unsupported(device, command, buffer):
            struct.pack_into('=I', buffer, 0, 0x80000000)
        with patch.object(bridge.fcntl, 'ioctl', unsupported):
            self.assertIsNone(bridge.RequestWait(123).poller)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux request readiness')
    def test_pending_and_delayed_work_wake_real_poll_without_idle_wakeups(self):
        reader, writer = os.pipe()
        try:
            with patch.object(bridge.fcntl, 'ioctl', capabilities):
                wait = bridge.RequestWait(reader)
            # Work queued before poll() must not be lost.
            os.write(writer, b'x')
            self.assertTrue(wait.wait(None))
            os.read(reader, 1)
            start = time.monotonic()
            self.assertFalse(wait.wait(0.08))
            self.assertGreaterEqual(time.monotonic() - start, 0.06)
            def produce():
                time.sleep(0.04); os.write(writer, b'y')
            thread = threading.Thread(target=produce)
            thread.start()
            self.assertTrue(wait.wait(1))
            thread.join(); self.assertEqual(os.read(reader, 1), b'y')
            self.assertFalse(wait.wait(0))
        finally:
            os.close(reader); os.close(writer)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux request readiness')
    def test_disconnected_or_invalid_channel_fails_for_service_recovery(self):
        reader, writer = os.pipe()
        with patch.object(bridge.fcntl, 'ioctl', capabilities):
            wait = bridge.RequestWait(reader)
        os.close(writer)
        try:
            with self.assertRaises(OSError) as failure: wait.wait(0)
            self.assertEqual(failure.exception.errno, errno.ENODEV)
        finally:
            os.close(reader)
        with self.assertRaises(OSError): wait.wait(0)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux request readiness')
    def test_idle_bridge_waits_until_signal_deadline_without_extra_ioctl_checks(self):
        class StopReview(Exception): pass
        calls, waits = [], []
        def ioctl(device, command, buffer):
            calls.append(command)
            # No pending work. Older kernel capability forces the rate-limited
            # mode, but the bridge still supplies the actual signal deadline.
            if command == bridge.GET_CAPABILITIES: raise OSError(errno.ENOTTY, 'old')
        def wait(self, timeout):
            waits.append(timeout)
            raise StopReview()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'host.sock'
            server = socket.socket(socket.AF_UNIX); server.bind(str(path)); server.listen(1)
            errors = []
            def host():
                try:
                    server.settimeout(3)
                    client, _ = server.accept()
                    with client:
                        client.settimeout(3)
                        self.assertEqual(json.loads(client.makefile('rb').readline()), {'action': 'wifi-status'})
                        client.sendall(b'{"ok":true,"locationAuthorized":false}\n')
                except BaseException as error: errors.append(error)
            thread = threading.Thread(target=host); thread.start()
            try:
                with tempfile.TemporaryFile() as device, patch('builtins.open', return_value=device), \
                        patch.object(bridge.fcntl, 'ioctl', ioctl), patch.object(bridge.RequestWait, 'wait', wait):
                    with self.assertRaises(StopReview): bridge.serve(str(path))
                thread.join(4)
                self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
                self.assertEqual(calls, [bridge.GET_CAPABILITIES, bridge.GET_CONNECTION, bridge.GET_SCAN])
                self.assertEqual(len(waits), 1)
                self.assertTrue(3 < waits[0] <= 5)
            finally:
                server.close()

    @unittest.skipUnless(sys.platform == 'linux', 'Linux request readiness')
    def test_once_bridge_handles_work_arriving_during_wait_and_clears_buffer(self):
        reader, writer = os.pipe()
        ready = threading.Event()
        calls, buffers, errors = [], [], []
        request_reads = 0
        def ioctl(device, command, buffer):
            nonlocal request_reads
            calls.append(command)
            if command == bridge.GET_CAPABILITIES:
                capabilities(device, command, buffer)
            elif command == bridge.GET_CONNECTION:
                request_reads += 1
                if ready.is_set():
                    os.read(device.fileno(), 1)
                    buffer[:] = bridge.CONNECTION.pack(42, 2, 0, 0, 0, bytes(6), bytes(32), bytes(32))
                    buffers.append(buffer)
            elif command == bridge.CONNECTION_RESULT:
                self.assertEqual(struct.unpack('<IHH6s2x', buffer), (42, 0, 0, bytes(6)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'host.sock'
            server = socket.socket(socket.AF_UNIX); server.bind(str(path)); server.listen(2)
            def host():
                try:
                    time.sleep(0.05)
                    ready.set(); os.write(writer, b'x')
                    server.settimeout(3)
                    for action in ('wifi-disconnect', 'wifi-status'):
                        client, _ = server.accept()
                        with client:
                            client.settimeout(3)
                            self.assertEqual(json.loads(client.makefile('rb').readline()), {'action': action})
                            client.sendall(b'{"ok":true,"locationAuthorized":true,"networkIdentityAvailable":false}\n')
                except BaseException as error: errors.append(error)
            thread = threading.Thread(target=host); thread.start()
            try:
                with os.fdopen(reader, 'rb', buffering=0) as device, patch('builtins.open', return_value=device), \
                        patch.object(bridge.fcntl, 'ioctl', ioctl):
                    bridge.serve(str(path), once=True)
                thread.join(4)
                self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
                self.assertEqual(request_reads, 2)
                self.assertEqual(calls.count(bridge.GET_SCAN), 1)
                self.assertEqual(calls.count(bridge.CONNECTION_RESULT), 1)
                self.assertEqual(buffers, [bytearray(bridge.CONNECTION.size)])
            finally:
                os.close(writer); server.close()


if __name__ == '__main__':
    unittest.main()
