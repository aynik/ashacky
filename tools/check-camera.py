#!/usr/bin/env python3
"""Real Swift producer and Linux reader contract, using only generated pixels."""
import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import threading
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'guest/devices'))
from camera_shared import CameraStream, FRAME_BYTES

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--peer',type=Path,required=True)
    p.add_argument('--memory',type=Path,required=True)
    p.add_argument('--library',type=Path,required=True)
    args=p.parse_args()
    process=subprocess.Popen([str(args.peer),str(args.memory),'--peer'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    timer=threading.Timer(10,process.kill);timer.start()
    class Stream(CameraStream):
        def rpc(self,action,**fields):
            process.stdin.write(json.dumps(dict(action=action,**fields))+'\n');process.stdin.flush()
            return json.loads(process.stdout.readline(4096))
    try:
        with Stream(resource=args.memory,library=args.library) as stream:
            for value in range(1,8):
                pixels=stream.frame()
                assert pixels[:1280*720].tobytes()==bytes([value])*(1280*720)
                assert pixels[1280*720:].tobytes()==bytes([128])*(1280*360)
        assert bytes(stream.pixels)==bytes(FRAME_BYTES)
        process.stdin.close();process.wait(timeout=3)
        assert process.returncode==0
        with args.memory.open('rb') as f:
            f.seek(4096);assert not any(f.read(3*2*1024*1024))
        print('Swift/Python camera descriptors, UUIDs, shared pixels, acknowledgements and clearing passed')
    finally:
        if process.poll() is None:process.kill();process.wait()
        process.stdout.close();timer.cancel()
if __name__=='__main__':main()
