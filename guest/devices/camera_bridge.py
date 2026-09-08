"""Feed bounded, framed NV12 camera data into a standard V4L2 device.

Prototype transport is a private SSH-forwarded Unix socket. Frames stay in
memory and are never saved. The host stops each test stream after 30 seconds.
"""
import argparse
import json
import socket
import struct
import subprocess

HEADER = struct.Struct('<8sIIII')


def exact(stream, size, allow_eof=False):
    data = bytearray()
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            if allow_eof and not data:
                return None
            raise ValueError('Truncated camera stream')
        data.extend(part)
    return data


def frame(stream):
    header = exact(stream, HEADER.size, allow_eof=True)
    if header is None:
        return None
    magic, width, height, length, sequence = HEADER.unpack(header)
    if magic != b'LHCV0001' or not 0 < width <= 1280 or not 0 < height <= 720:
        raise ValueError('Invalid camera frame header')
    if width % 2 or height % 2 or length != width * height * 3 // 2:
        raise ValueError('Invalid NV12 frame dimensions')
    return width, height, sequence, exact(stream, length)


def serve(path, device):
    writer = None
    frames = 0
    size = None
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(10)
            client.connect(path)
            stream = client.makefile('rb')
            while True:
                item = frame(stream)
                if item is None:
                    break
                width, height, sequence, pixels = item
                if size is None:
                    size = (width, height)
                    writer = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error',
                        '-f', 'rawvideo', '-pixel_format', 'nv12', '-video_size', f'{width}x{height}',
                        '-framerate', '30', '-i', 'pipe:0', '-an', '-pix_fmt', 'yuyv422',
                        '-f', 'v4l2', device], stdin=subprocess.PIPE)
                if size != (width, height) or sequence != frames:
                    raise ValueError('Camera format or sequence changed unexpectedly')
                writer.stdin.write(pixels)
                frames += 1
    finally:
        if writer:
            try:
                writer.stdin.close()
            except BrokenPipeError:
                pass
            try:
                code = writer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                writer.kill(); writer.wait(); raise
            if code:
                raise RuntimeError('V4L2 feeder failed')
    if not frames:
        raise RuntimeError('No camera frames received; check host permission/session')
    print(json.dumps({'frames_forwarded': frames, 'width': size[0], 'height': size[1],
                      'v4l2_device': device, 'recording_saved': False}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', default='/run/linuxhost-camera-test.sock')
    parser.add_argument('--device', default='/dev/video10')
    args = parser.parse_args()
    serve(args.socket, args.device)
