"""Keep V4L2 discoverable while capturing from macOS only on reader demand.

Idle frames are generated black pixels, never cached camera images. This
service uses v4l2loopback 0.15.4's usage event and shared camera memory.
"""
import argparse
import json
import signal
import subprocess
import threading
import time
from camera_shared import CameraStream


def serve(path, device, monitor_path):
    width, height = 1280, 720
    black = bytes([16]) * (width * height) + bytes([128]) * (width * height // 2)
    active = threading.Event()
    stopping = threading.Event()
    changed = threading.Event()
    writer_done = threading.Event()
    monitor_done = threading.Event()
    def stop(*_):
        stopping.set()
        changed.set()
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
                changed.set()
        finally:
            active.clear()
            monitor_done.set()
            changed.set()  # Never keep capturing after loss of demand tracking.
    def watch_writer():
        writer.wait()
        writer_done.set()
        changed.set()
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    writer_watcher = threading.Thread(target=watch_writer, daemon=True)
    writer_watcher.start()
    try:
        # Prime FFmpeg and establish output ownership/capture capability. No
        # physical camera is opened during this initialization.
        for _ in range(4):
            writer.stdin.write(black)
        writer.stdin.flush()
        print(json.dumps({'camera_device_ready': True, 'host_capture_started': False}), flush=True)
        last_error = None
        while not stopping.is_set():
            # Clear before checking predicates so concurrent demand, stop or
            # child-exit notifications cannot be lost before this wait.
            changed.clear()
            if stopping.is_set():
                break
            if writer_done.is_set():
                raise RuntimeError('Camera producer exited')
            if monitor_done.is_set():
                raise RuntimeError('Camera demand monitor exited')
            if not active.is_set():
                changed.wait()
                continue
            received = 0
            try:
                with CameraStream(path) as stream:
                    while active.is_set() and not stopping.is_set():
                        pixels = stream.frame()
                        if not active.is_set() or stopping.is_set():
                            break
                        writer.stdin.write(pixels)
                        writer.stdin.flush()
                        received += 1
                        last_error = None
            except (OSError, ValueError, RuntimeError) as error:
                category = type(error).__name__
                if category != last_error:
                    print(json.dumps({'camera_transport_error': category}), flush=True)
                    last_error = category
            finally:
                # Closing the stream requests host stop. Replace the final
                # camera frame with black instead of leaving a private image.
                for _ in range(4):
                    writer.stdin.write(black)
                writer.stdin.flush()
                if received:
                    print(json.dumps({'camera_stream_frames': received,
                                      'reader_still_active': active.is_set()}), flush=True)
            if active.is_set():
                stopping.wait(.5)  # Bounded recovery after a failed active stream.
    finally:
        stopping.set()
        changed.set()
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
        watcher.join(timeout=1)
        writer_watcher.join(timeout=1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', default='/run/ashacky-control/camera.sock')
    parser.add_argument('--device', default='/dev/video10')
    parser.add_argument('--monitor', default='/opt/linuxhost-probe/camera-readers')
    args = parser.parse_args()
    serve(args.socket, args.device, args.monitor)
