import contextlib
import io
import json
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'guest/devices'))
import camera_demand
from camera_bridge import HEADER


class CameraEventTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'linux', 'Linux camera-service lifecycle')
    def test_reader_demand_starts_capture_and_replaces_last_frame_with_black(self):
        # Exercise both sides of the idle wait using private sockets and real
        # child pipes. Only generated pixels and aggregate frame markers exist.
        spawn = subprocess.Popen
        children, handlers, failures = [], {}, []
        output, markers = io.StringIO(), queue.Queue()
        connected, disconnected, done = threading.Event(), threading.Event(), threading.Event()
        black_size = 1280 * 720 * 3 // 2
        pixels = bytes([90]) * black_size
        with tempfile.TemporaryDirectory() as directory:
            endpoint = str(Path(directory) / 'camera.sock')
            server = socket.socket(socket.AF_UNIX)
            server.bind(endpoint); server.listen(); server.settimeout(3)
            def host():
                try:
                    client, _ = server.accept()
                    with client:
                        client.settimeout(2); connected.set()
                        sequence = 0
                        while not done.is_set():
                            client.sendall(HEADER.pack(b'LHCV0001', 1280, 720, len(pixels), sequence) + pixels)
                            sequence += 1
                            done.wait(.02)
                except (BrokenPipeError, ConnectionResetError):
                    disconnected.set()
                except Exception as error:
                    if not done.is_set(): failures.append(error)
            def start(command, **kwargs):
                if command[0] == 'ffmpeg':
                    code = ('import sys\n'
                        f'size={black_size}\n'
                        'while True:\n'
                        ' frame=sys.stdin.buffer.read(size)\n'
                        ' if not frame: break\n'
                        ' assert len(frame)==size\n'
                        ' print("black" if frame[0]==16 else "generated",flush=True)\n')
                    child = spawn([sys.executable, '-u', '-c', code],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                else:
                    code = 'import sys\nprint(\'{"camera_reader_active":false}\',flush=True)\nfor line in sys.stdin: print(line,end="",flush=True)\n'
                    child = spawn([sys.executable, '-u', '-c', code],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                children.append(child)
                return child
            def serve():
                try: camera_demand.serve(endpoint, '/no-camera-device', 'usage-fixture')
                except Exception as error: failures.append(error)
            host_thread = threading.Thread(target=host)
            host_thread.start()
            marker_thread = None
            with patch.object(camera_demand.subprocess, 'Popen', start), \
                    patch.object(camera_demand.signal, 'signal', lambda number, handler: handlers.__setitem__(number, handler)), \
                    contextlib.redirect_stdout(output):
                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    deadline = time.monotonic() + 3
                    while 'camera_device_ready' not in output.getvalue() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertIn('camera_device_ready', output.getvalue())
                    def read_markers():
                        for line in children[0].stdout: markers.put(line.decode().strip())
                    marker_thread = threading.Thread(target=read_markers)
                    marker_thread.start()
                    for _ in range(4): self.assertEqual(markers.get(timeout=2), 'black')
                    self.assertFalse(connected.is_set(), 'Idle service opened the camera')
                    children[1].stdin.write(json.dumps({'camera_reader_active': True}) + '\n')
                    children[1].stdin.flush()
                    self.assertTrue(connected.wait(2), 'Reader event did not wake idle service')
                    self.assertEqual(markers.get(timeout=2), 'generated')
                    children[1].stdin.write(json.dumps({'camera_reader_active': False}) + '\n')
                    children[1].stdin.flush()
                    self.assertTrue(disconnected.wait(2), 'Reader closure did not stop the stream')
                    black_frames = 0
                    while black_frames < 4:
                        kind = markers.get(timeout=2)
                        if kind == 'black': black_frames += 1
                        else: self.assertEqual(black_frames, 0, 'Private frame followed idle black pixels')
                    self.assertTrue(thread.is_alive(), 'Demand closure terminated the service')
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                    thread.join(3)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(failures, [])
                finally:
                    done.set(); server.close()
                    if signal.SIGTERM in handlers: handlers[signal.SIGTERM](signal.SIGTERM, None)
                    for child in children:
                        if child.poll() is None: child.kill()
                        child.wait(timeout=3)
                    thread.join(3); host_thread.join(3)
                    if marker_thread: marker_thread.join(3)
                    for child in children:
                        for stream in (child.stdin, child.stdout):
                            if stream and not stream.closed: stream.close()

    @unittest.skipUnless(sys.platform == 'linux', 'Linux camera-service lifecycle')
    def test_idle_stop_and_child_failures_wake_and_reap_without_capture(self):
        # Real child pipes replace FFmpeg and the usage monitor. The producer
        # discards generated black frames; there is no camera or host endpoint.
        spawn = subprocess.Popen
        for ending in ('stop', 'writer', 'monitor'):
            with self.subTest(ending=ending):
                children, handlers, failures = [], {}, []
                output = io.StringIO()
                def start(command, **kwargs):
                    if command[0] == 'ffmpeg':
                        code = 'import sys\nwhile sys.stdin.buffer.read(65536): pass\n'
                        process = spawn([sys.executable, '-u', '-c', code], stdin=subprocess.PIPE)
                    else:
                        code = 'import sys\nprint(\'{"camera_reader_active":false}\',flush=True)\nfor line in sys.stdin: print(line,end="",flush=True)\n'
                        process = spawn([sys.executable, '-u', '-c', code],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                    children.append(process)
                    return process
                def serve():
                    try: camera_demand.serve('/no-camera-endpoint', '/no-camera-device', 'usage-fixture')
                    except Exception as error: failures.append(error)
                with patch.object(camera_demand.subprocess, 'Popen', start), \
                        patch.object(camera_demand.signal, 'signal', lambda number, handler: handlers.__setitem__(number, handler)), \
                        patch.object(camera_demand.socket, 'socket') as connection, contextlib.redirect_stdout(output):
                    thread = threading.Thread(target=serve)
                    thread.start()
                    try:
                        deadline = time.monotonic() + 3
                        while 'camera_device_ready' not in output.getvalue() and time.monotonic() < deadline:
                            time.sleep(.01)
                        self.assertIn('camera_device_ready', output.getvalue())
                        time.sleep(.12)
                        self.assertTrue(thread.is_alive())
                        connection.assert_not_called()
                        began = time.monotonic()
                        if ending == 'stop': handlers[signal.SIGTERM](signal.SIGTERM, None)
                        else: children[0 if ending == 'writer' else 1].terminate()
                        thread.join(3)
                        self.assertFalse(thread.is_alive(), 'Idle camera service missed its wakeup')
                        self.assertLess(time.monotonic() - began, 2)
                        connection.assert_not_called()
                        self.assertTrue(all(child.poll() is not None for child in children))
                        if ending == 'stop': self.assertEqual(failures, [])
                        else:
                            self.assertEqual(len(failures), 1)
                            self.assertIsInstance(failures[0], RuntimeError)
                            self.assertIn('exited', str(failures[0]))
                    finally:
                        if signal.SIGTERM in handlers: handlers[signal.SIGTERM](signal.SIGTERM, None)
                        for child in children:
                            if child.poll() is None: child.kill()
                            child.wait(timeout=3)
                            for stream in (child.stdin, child.stdout):
                                if stream and not stream.closed: stream.close()
                        thread.join(3)


if __name__ == '__main__':
    unittest.main()
