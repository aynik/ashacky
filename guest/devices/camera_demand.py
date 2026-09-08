"""Keep V4L2 discoverable while capturing from macOS only on reader demand.

Idle frames are generated black pixels, never cached camera images. This
prototype uses v4l2loopback 0.15.4's usage event and the private host socket.
"""
import argparse
import json
import signal
import socket
import subprocess
import threading
import time
from camera_bridge import frame


def serve(path, device, monitor_path):
    width, height = 1280, 720
    black = bytes([16]) * (width * height) + bytes([128]) * (width * height // 2)
    active = threading.Event()
    stopping = threading.Event()
    def stop(*_):
        stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    writer = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-filter_threads', '1',
        '-analyzeduration', '0', '-probesize', '32', '-f', 'rawvideo',
        '-pixel_format', 'nv12', '-video_size', f'{width}x{height}', '-framerate', '30',
        '-i', 'pipe:0', '-an', '-pix_fmt', 'yuyv422', '-threads', '1', '-f', 'v4l2', device], stdin=subprocess.PIPE)
    monitor = subprocess.Popen([monitor_path, device], stdout=subprocess.PIPE, text=True)
    def watch():
        try:
            for line in monitor.stdout:
                state = json.loads(line)
                if state['camera_reader_active']:
                    active.set()
                else:
                    active.clear()
        finally:
            active.clear()
            stopping.set()  # Never keep capturing after loss of demand tracking.
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        # Prime FFmpeg and establish output ownership/capture capability. No
        # physical camera is opened during this initialization.
        for _ in range(4):
            writer.stdin.write(black)
        writer.stdin.flush()
        print(json.dumps({'camera_device_ready': True, 'host_capture_started': False}), flush=True)
        last_error = None
        while not stopping.is_set():
            if writer.poll() is not None:
                raise RuntimeError('Camera producer exited')
            if not active.wait(.1):
                continue
            received = 0
            try:
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(5)  # Camera startup can exceed one second.
                    client.connect(path)
                    with client.makefile('rb') as stream:
                        while active.is_set() and not stopping.is_set():
                            item = frame(stream)
                            if item is None:
                                break
                            w, h, sequence, pixels = item
                            client.settimeout(1)  # Bound cancellation once streaming.
                            if (w, h) != (width, height) or sequence != received:
                                raise ValueError('Unexpected camera format/sequence')
                            if not active.is_set() or stopping.is_set():
                                break
                            writer.stdin.write(pixels)
                            received += 1
                            last_error = None
            except (OSError, ValueError) as error:
                category = type(error).__name__
                if category != last_error:
                    print(json.dumps({'camera_transport_error': category}), flush=True)
                    last_error = category
            finally:
                # Closing socket tells the host to stop. Replace the final
                # camera frame with black instead of leaving a private image.
                for _ in range(4):
                    writer.stdin.write(black)
                writer.stdin.flush()
                if received:
                    print(json.dumps({'camera_stream_frames': received,
                                      'reader_still_active': active.is_set()}), flush=True)
            if active.is_set():
                stopping.wait(.5)  # Bounded retry / renew 30-second host stream.
    finally:
        monitor.terminate()
        try:
            monitor.wait(timeout=3)
        except subprocess.TimeoutExpired:
            monitor.kill(); monitor.wait()
        try:
            writer.stdin.close()
        except BrokenPipeError:
            pass
        try:
            writer.wait(timeout=3)
        except subprocess.TimeoutExpired:
            writer.kill(); writer.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', default='/run/linuxhost-camera-test.sock')
    parser.add_argument('--device', default='/dev/video10')
    parser.add_argument('--monitor', default='/root/linuxhost-dev/camera-readers')
    args = parser.parse_args()
    serve(args.socket, args.device, args.monitor)
