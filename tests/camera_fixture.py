"""Generated pixels and local sockets only; never access the physical camera."""
import ctypes
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]

class Memory:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='camera-fixture-')
        self.root = Path(self.temp.name)
        self.library = self.root/'camera-memory.so'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        str(ROOT/'host/common/CameraMemory.c'), '-o', str(self.library)], check=True)
        self.path = self.root/'frames'
        with self.path.open('wb') as f: f.truncate(8*1024*1024)
        self.lib = ctypes.CDLL(str(self.library))
        self.lib.ashacky_camera_map.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.lib.ashacky_camera_map.restype = ctypes.c_void_p
        self.lib.ashacky_camera_unmap.argtypes = [ctypes.c_void_p]
        self.lib.ashacky_camera_clear.argtypes = [ctypes.c_void_p]
        self.lib.ashacky_camera_publish.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint64,
                                                   ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        self.lib.ashacky_camera_copy.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t]
        self.address = self.lib.ashacky_camera_map(bytes(self.path), 1)
        assert self.address
        self.id = str(uuid.uuid4()).upper()
        self.generation = ctypes.create_string_buffer(uuid.UUID(self.id).bytes,16)
        self.y = ctypes.create_string_buffer(bytes([90])*1344*720)
        self.uv = ctypes.create_string_buffer(bytes([128])*1344*360)
    def publish(self, slot=0, sequence=1):
        return self.lib.ashacky_camera_publish(self.address, slot, self.generation, sequence,self.y,1344,self.uv,1344)
    def close(self):
        self.lib.ashacky_camera_unmap(self.address); self.temp.cleanup()

class Host:
    def __init__(self, memory):
        self.memory = memory
        self.endpoint = str(memory.root/'camera.sock')
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(self.endpoint); self.server.listen(); self.server.settimeout(.1)
        self.connected = threading.Event(); self.disconnected = threading.Event(); self.done = threading.Event()
        self.calls = []; self.failures = []; self.sequence = 0; self.reply_override = None
        self.thread = threading.Thread(target=self.run); self.thread.start()
    def run(self):
        try:
            while not self.done.is_set():
                try: client,_ = self.server.accept()
                except socket.timeout: continue
                with client:
                    client.settimeout(2)
                    with client.makefile('rb') as f: data=f.readline(4096)
                    request=json.loads(data); self.calls.append(request)
                    if request['action']=='camera-start':
                        self.connected.set()
                        reply=dict(ok=True,stream=self.memory.id,width=1280,height=720,format='nv12',memoryBytes=8*1024*1024)
                    elif request['action']=='camera-next':
                        assert request['stream']==self.memory.id and request['ack']==self.sequence
                        self.done.wait(.02)
                        self.sequence+=1; self.memory.publish(sequence=self.sequence)
                        reply=dict(ok=True,stream=self.memory.id,slot=0,sequence=self.sequence,width=1280,height=720,format='nv12',bytes=1280*720*3//2)
                        if self.reply_override:reply.update(self.reply_override)
                    else:
                        assert request['action']=='camera-stop' and request['stream']==self.memory.id
                        self.disconnected.set(); self.memory.lib.ashacky_camera_clear(self.memory.address)
                        reply=dict(ok=True)
                    client.sendall(json.dumps(reply).encode()+b'\n')
        except Exception as error:
            if not self.done.is_set(): self.failures.append(error)
    def close(self):
        self.done.set(); self.thread.join(3); self.server.close()
        assert not self.thread.is_alive()
