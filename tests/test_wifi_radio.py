import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'guest/devices'))
import wifi_events
import wifi_power


class WiFiRadioTests(unittest.TestCase):
    def test_host_capability_requires_fresh_event_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            self.assertIsNone(wifi_events.host_revision(path)[0])
            value = {'ok': True, 'statusTransport': 'virtio-serial', 'wifiRevision': 'epoch:1',
                     'active': True, 'wakeGeneration': 0}
            path.write_text(json.dumps(value))
            self.assertEqual(wifi_events.host_revision(path)[0], ('epoch:1', True, 0))
            os.utime(path, (1, 1))
            self.assertIsNone(wifi_events.host_revision(path)[0])
            for change in ({'statusTransport': 'rpc-poll'}, {'wifiRevision': ''},
                           {'wifiRevision': 1}, {'wifiRevision': 'x' * 129}, {'ok': False}):
                path.write_text(json.dumps(dict(value, **change)))
                self.assertIsNone(wifi_events.host_revision(path)[0])

    def test_startup_echo_and_newer_choices_do_not_replay_old_commands(self):
        radio = wifi_power.RadioIntent(7, 1)
        writes = []
        write = lambda index, soft: writes.append((index, soft))
        self.assertFalse(radio.observe(1))
        radio.apply(True, write)
        self.assertEqual(writes, [(7, 0)])
        self.assertFalse(radio.observe(0))  # Our own unblock is not a host command.
        self.assertTrue(radio.observe(1)); old = radio.pending
        self.assertTrue(radio.observe(0)); newer = radio.pending
        radio.attempted(old)
        self.assertEqual(radio.pending, newer)
        radio.apply(False, write)  # An old reply cannot overwrite the new choice.
        self.assertEqual(writes, [(7, 0)])
        radio.attempted(newer); radio.apply(True, write)
        self.assertIsNone(radio.pending)
        self.assertTrue(radio.observe(1)); failed = radio.pending
        radio.attempted(failed); radio.apply(True, write)
        self.assertIsNone(radio.pending)  # No blind retry after uncertain failure.
        self.assertFalse(radio.observe(0))
        radio.inactive()
        self.assertFalse(radio.observe(1))
        self.assertIsNone(radio.pending)

    @unittest.skipUnless(sys.platform == 'linux', 'GIO and Linux event integration')
    def test_real_events_rpc_races_echoes_failures_and_idle_heartbeats(self):
        from gi.repository import GLib
        context = GLib.MainContext.default()
        def pump(condition=lambda: False, duration=0.2, required=False):
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                while context.pending(): context.iteration(False)
                if condition(): return
                time.sleep(0.002)
            if required: self.assertTrue(condition(), 'Event operation did not finish')

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory); status = directory / 'status.json'; endpoint = directory / 'host.sock'
            def publish(revision, active=True):
                temporary = directory / 'status.tmp'
                temporary.write_text(json.dumps({'ok': True, 'statusTransport': 'virtio-serial',
                    'wifiRevision': revision, 'active': active, 'wakeGeneration': 0}))
                temporary.replace(status)
            publish('epoch:1')
            server = socket.socket(socket.AF_UNIX); server.bind(str(endpoint)); server.listen(5); server.settimeout(.1)
            stop = threading.Event(); off_entered = threading.Event(); allow_off = threading.Event()
            state = {'powered': True, 'holdOff': False, 'failNext': False}
            calls, errors = [], []
            def host():
                while not stop.is_set():
                    try: client, _ = server.accept()
                    except socket.timeout: continue
                    except OSError: break
                    try:
                        with client:
                            client.settimeout(3)
                            with client.makefile('rb') as stream: request = json.loads(stream.readline())
                            calls.append(request)
                            reply = {'ok': True}
                            if request['action'] == 'wifi-power':
                                if state['holdOff'] and not request['enabled']:
                                    off_entered.set()
                                    if not allow_off.wait(3): raise TimeoutError('Test release missing')
                                if state['failNext']:
                                    state['failNext'] = False
                                    reply = {'ok': False, 'error': 'Synthetic operation failure'}
                                else: state['powered'] = request['enabled']
                            elif request['action'] == 'wifi-status': reply['powered'] = state['powered']
                            else: raise AssertionError('Unexpected host operation')
                            client.sendall(json.dumps(reply).encode() + b'\n')
                    except BaseException as error: errors.append(error)
            thread = threading.Thread(target=host, daemon=True); thread.start()
            reader, writer = os.pipe(); os.set_blocking(reader, False)
            bridge = wifi_power.WiFiPower(str(endpoint))
            bridge.events.path = status; bridge.rfkill = reader
            bridge.radio = wifi_power.RadioIntent(7, 1)
            writes = []
            def radio_event(soft, index=7, kind=1):
                os.write(writer, wifi_power.RFKILL.pack(index, kind, 2, soft, 0))
            def write_radio(index, soft):
                writes.append((index, soft)); radio_event(soft, index)
            bridge.write_radio = write_radio
            bridge.sources.append(GLib.io_add_watch(reader, GLib.PRIORITY_DEFAULT,
                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, bridge.radio_ready))
            try:
                bridge.events.watch()
                pump(lambda: bridge.radio.initialized and not bridge.busy, 2, True)
                pump()
                self.assertEqual(writes, [(7, 0)])
                self.assertEqual(calls, [{'action': 'wifi-status'}])
                # Atomic telemetry heartbeats, and unrelated radio devices, must
                # not generate another host status request.
                for _ in range(3): publish('epoch:1'); pump(duration=.06)
                radio_event(1, index=99); radio_event(1, index=7, kind=2); pump()
                self.assertEqual(len(calls), 1)
                state['holdOff'] = True; radio_event(1)
                pump(off_entered.is_set, 2, True)
                radio_event(0); publish('epoch:2'); pump(duration=.1)
                allow_off.set()
                pump(lambda: state['powered'] and bridge.radio.pending is None and not bridge.busy, 3, True)
                pump()
                self.assertEqual([c['enabled'] for c in calls if c['action'] == 'wifi-power'], [False, True])
                self.assertEqual(writes, [(7, 0)])  # No old-reply flash back to off.
                # An external host power transition updates Linux without an echo
                # mutation back to macOS.
                state['powered'] = False; publish('epoch:3')
                pump(lambda: writes[-1] == (7, 1), 2, True); pump()
                self.assertEqual(len([c for c in calls if c['action'] == 'wifi-power']), 2)
                publish('epoch:4', active=False); pump(); before = len(calls)
                radio_event(0); pump()
                self.assertEqual(len(calls), before)
                publish('epoch:5'); pump(lambda: len(writes) == 3, 2, True); pump()
                self.assertEqual(len([c for c in calls if c['action'] == 'wifi-power']), 2)
                # Failed command, successful readback: reflect actual state once
                # and don't turn that feedback into another mutation.
                state['failNext'] = True; radio_event(0)
                pump(lambda: len(writes) == 4, 2, True); pump()
                self.assertEqual([c['enabled'] for c in calls if c['action'] == 'wifi-power'], [False, True, True])
                self.assertIsNone(bridge.radio.pending)
                self.assertTrue(bridge.failed)
                self.assertIsNone(bridge.error)
                # Missing capability starts the bounded compatibility path.
                publish(None); pump(lambda: bridge.events.mode == 'compatibility polling', 2, True)
                before = len(calls)
                pump(lambda: len(calls) > before and not bridge.busy, 1.5, True)
                self.assertIsNone(bridge.error)
                self.assertEqual(errors, [])
            finally:
                allow_off.set(); bridge.close(); os.close(writer)
                stop.set(); server.close(); thread.join(4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(bridge.events.sources, {})
            self.assertEqual(bridge.sources, [])
            self.assertIsNone(bridge.rfkill)


if __name__ == '__main__':
    unittest.main()
