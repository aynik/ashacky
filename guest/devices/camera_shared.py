"""Camera frame descriptors over private RPC, pixels through a read-only PCI BAR."""
import ctypes
import json
import os
from pathlib import Path
import socket
import stat
import uuid

MEMORY_BYTES = 8 * 1024 * 1024
FRAME_BYTES = 1280 * 720 * 3 // 2


def resource_path(devices=Path('/sys/bus/pci/devices')):
    candidates = []
    for device in devices.iterdir():
        if ((device / 'vendor').read_text().strip(), (device / 'device').read_text().strip()) != ('0x1234', '0x11f0'):
            continue
        start, end, flags = (int(part, 16) for part in (device / 'resource').read_text().splitlines()[0].split())
        if start and end - start + 1 == MEMORY_BYTES and flags & 0x200:
            candidates.append(device)
    if len(candidates) != 1:
        raise RuntimeError('Expected exactly one 8 MiB camera memory device')
    device = candidates[0]
    if (device / 'enable').read_text().strip() == '0':
        (device / 'enable').write_text('1')
    # The video BAR is separately granted to the desktop. Camera is root-only.
    for name in ('resource0', 'resource0_wc'):
        path = device / name
        info = path.stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            os.chown(path, 0, 0); os.chmod(path, 0o600)
    return device / 'resource0_wc'


class CameraStream:
    def __init__(self, endpoint='/run/ashacky-control/camera.sock', *, resource=None, library=None):
        self.endpoint = endpoint
        self.memory = None
        self.stream = None
        self.sequence = 0
        self.lib = ctypes.CDLL(str(library or Path(__file__).with_name('camera-memory.so')))
        self.lib.ashacky_camera_map.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.lib.ashacky_camera_map.restype = ctypes.c_void_p
        self.lib.ashacky_camera_unmap.argtypes = [ctypes.c_void_p]
        self.lib.ashacky_camera_unmap.restype = None
        self.lib.ashacky_camera_copy.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                                ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t]
        self.lib.ashacky_camera_copy.restype = ctypes.c_int
        self.pixels = ctypes.create_string_buffer(FRAME_BYTES)
        self.resource = resource

    def rpc(self, action, **fields):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(6 if action == 'camera-start' else 2)
            client.connect(self.endpoint)
            client.sendall(json.dumps(dict(action=action, **fields)).encode() + b'\n')
            with client.makefile('rb') as reader:
                line = reader.readline(4097)
            if len(line) > 4096 or not line.endswith(b'\n'):
                raise ValueError('Invalid camera reply')
            reply = json.loads(line)
            if not isinstance(reply, dict) or reply.get('ok') is not True:
                raise ValueError('Host camera request failed')
            return reply

    def __enter__(self):
        self.memory = self.lib.ashacky_camera_map(os.fsencode(self.resource or resource_path()), 0)
        if not self.memory:
            raise OSError('Cannot map camera memory read-only')
        try:
            reply = self.rpc('camera-start')
            self.stream = str(uuid.UUID(reply['stream'])).upper()
            self.generation = ctypes.create_string_buffer(uuid.UUID(self.stream).bytes, 16)
            if (reply.get('width'), reply.get('height'), reply.get('format'), reply.get('memoryBytes')) != (1280, 720, 'nv12', MEMORY_BYTES):
                raise ValueError('Unexpected camera format')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def frame(self):
        reply = self.rpc('camera-next', stream=self.stream, ack=self.sequence)
        slot, sequence = reply.get('slot'), reply.get('sequence')
        if (reply.get('stream') != self.stream or type(slot) is not int or not 0 <= slot < 3 or
                type(sequence) is not int or not self.sequence < sequence <= 0x7fffffff or
                (reply.get('width'), reply.get('height'), reply.get('bytes'), reply.get('format')) != (1280, 720, FRAME_BYTES, 'nv12')):
            raise ValueError('Invalid camera frame descriptor')
        if self.lib.ashacky_camera_copy(self.memory, slot, self.generation, sequence, self.pixels, FRAME_BYTES) != 0:
            raise ValueError('Revoked or inconsistent camera frame')
        self.sequence = sequence
        # Stable scratch storage; next() acknowledges only after the caller wrote
        # this frame. Neither the shared slot nor this scratch is a retained image.
        return memoryview(self.pixels).cast('B')

    def __exit__(self, *_):
        try:
            if self.stream:
                self.rpc('camera-stop', stream=self.stream)
        except (OSError, ValueError):
            pass  # The host's five-second demand lease also stops lost clients.
        finally:
            self.stream = None
            if self.memory:
                self.lib.ashacky_camera_unmap(self.memory)
                self.memory = None
            ctypes.memset(self.pixels, 0, FRAME_BYTES)
