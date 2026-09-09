import ctypes
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'guest/devices'))
from camera_shared import CameraStream, FRAME_BYTES, resource_path
from camera_fixture import Host, Memory

class CameraMemoryTests(unittest.TestCase):
    def setUp(self): self.memory=Memory()
    def tearDown(self): self.memory.close()
    def test_strided_planes_commit_identity_bounds_and_zeroing(self):
        m=self.memory;output=ctypes.create_string_buffer(FRAME_BYTES)
        def copy(slot=0, sequence=1, generation=None, size=FRAME_BYTES):
            return m.lib.ashacky_camera_copy(m.address,slot,generation or m.generation,sequence,output,size)
        self.assertEqual(copy(),-2)
        self.assertEqual(m.publish(),0); self.assertEqual(copy(),0)
        self.assertEqual(output.raw[:1280*720],bytes([90])*(1280*720))
        self.assertEqual(output.raw[1280*720:],bytes([128])*(1280*360))
        self.assertEqual(copy(slot=3),-1);self.assertEqual(copy(size=FRAME_BYTES+1),-1)
        self.assertEqual(copy(sequence=2),-2)
        self.assertEqual(copy(generation=ctypes.create_string_buffer(b'x'*16,16)),-2)
        m.lib.ashacky_camera_clear(m.address)
        self.assertEqual(copy(),-2)
        self.assertEqual(ctypes.string_at(m.address+4096+64,FRAME_BYTES),bytes(FRAME_BYTES))
    def test_descriptor_only_rpc_ack_and_stop(self):
        host=Host(self.memory)
        try:
            with CameraStream(host.endpoint,resource=self.memory.path,library=self.memory.library) as stream:
                for _ in range(4): self.assertEqual(stream.frame()[0],90)
                self.assertEqual(stream.sequence,4)
            self.assertTrue(host.disconnected.is_set())
            self.assertEqual([c['ack'] for c in host.calls if c['action']=='camera-next'],[0,1,2,3])
            self.assertLess(max(len(json.dumps(c)) for c in host.calls),256)
            self.assertEqual(host.failures,[])
        finally:host.close()
    def test_bad_descriptor_is_rejected_and_capture_stopped(self):
        for fields in ({'slot':3},{'sequence':True},{'bytes':FRAME_BYTES+1},{'stream':'other'}, {'sequence':999}):
            host=Host(self.memory)
            try:
                host.reply_override=fields
                with CameraStream(host.endpoint,resource=self.memory.path,library=self.memory.library) as stream:
                    with self.assertRaises(ValueError):stream.frame()
                self.assertTrue(host.disconnected.is_set())
            finally:
                host.close();Path(host.endpoint).unlink()

class CameraResourceTests(unittest.TestCase):
    def test_camera_bar_selection_does_not_grant_video_or_ambiguous_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def device(name,size):
                d=root/name;d.mkdir()
                for key,value in {'vendor':'0x1234','device':'0x11f0','resource':f'10000000 {0x10000000+size-1:x} 200',
                                  'enable':'0','resource0':'','resource0_wc':''}.items():
                    (d/key).write_text(value)
                return d
            video=device('video',64*1024*1024);camera=device('camera',8*1024*1024)
            with patch('camera_shared.os.chown') as owner, patch('camera_shared.os.chmod') as mode:
                self.assertEqual(resource_path(root),camera/'resource0_wc')
                self.assertEqual((video/'enable').read_text(),'0')
                self.assertEqual((camera/'enable').read_text(),'1')
                self.assertEqual({c.args[0] for c in mode.call_args_list},{camera/'resource0',camera/'resource0_wc'})
                self.assertTrue(all(c.args[1]==0o600 for c in mode.call_args_list))
                device('other-camera',8*1024*1024)
                with self.assertRaises(RuntimeError):resource_path(root)

if __name__=='__main__':unittest.main()
