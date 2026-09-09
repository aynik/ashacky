import contextlib
import errno
import io
import json
import os
from pathlib import Path
import platform
import select
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'guest/input'))
if platform.system()=='Linux':
    import trackpad


class Recorder:
    def __init__(self):self.events=[];self.frames=[]
    def write(self,*args):self.events.append(args)
    def syn(self):self.frames.append(time.monotonic())


@unittest.skipUnless(platform.system()=='Linux','Linux trackpad transport tests')
class TrackpadWaitTests(unittest.TestCase):
    def fixture(self):
        guest,host=socket.socketpair()
        guest.setblocking(False);host.settimeout(3)
        self.addCleanup(guest.close);self.addCleanup(host.close)
        pad=trackpad.Pad(Recorder());failures=[]
        def run():
            try:trackpad.serve(guest.fileno(),pad)
            except Exception as error:failures.append(error)
        thread=threading.Thread(target=run)
        def stop():
            try:host.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            thread.join(3)
            self.assertFalse(thread.is_alive(),'Trackpad wait did not exit on EOF')
        self.addCleanup(stop)
        return guest,host,pad,failures,thread

    def test_idle_heartbeat_and_stale_release_have_separate_deadlines(self):
        # Simulated monotonic time lets a contact arrive between heartbeats.
        # Partial incoming traffic must not postpone stale-contact release.
        now=[10.0];sent=[];waits=[];incoming=[];releases=[]
        contact=json.dumps({'touches':[[1,.25,.5]],'buttons':1}).encode()+b'\n'
        schedule=[(10.25,contact),(10.5,b' '),(10.75,b' '),(11.0,b' '),(11.1,b' '),(11.2,b' '),(12.01,b'')]
        recorder=Recorder();pad=trackpad.Pad(recorder)
        original=pad.frame
        def submit(points,buttons):
            if not points and not buttons and (pad.slots or pad.buttons):releases.append(now[0])
            original(points,buttons)
        def wait(read,write,error,timeout):
            self.assertEqual(read,[7]);self.assertFalse(write)
            self.assertIsNotNone(timeout)
            waits.append(timeout)
            deadline=now[0]+timeout
            if schedule and schedule[0][0]<=deadline:
                now[0],data=schedule.pop(0);incoming.append(data);return [7],[],[]
            now[0]=deadline;return [],[],[]
        def send(fd,data):sent.append((now[0],data));return len(data)
        with patch.object(trackpad.time,'monotonic',lambda:now[0]), \
                patch.object(trackpad.select,'select',wait),patch.object(trackpad.os,'read',lambda *a:incoming.pop(0)), \
                patch.object(trackpad.os,'write',send),patch.object(pad,'frame',submit):
            trackpad.serve(7,pad)
        self.assertEqual([t for t,data in sent if data==b'LH_INPUT_READY\n'],[10.,11.,12.])
        self.assertEqual([t for t,data in sent if data==b'LH_INPUT_APPLIED\n'],[10.25])
        self.assertEqual(releases,[11.25])
        self.assertGreaterEqual(max(waits),.75,'Idle wait retained a short periodic cap')
        self.assertFalse(pad.slots);self.assertEqual(pad.buttons,0)

    def test_socket_frames_acknowledge_only_submitted_contacts_and_release_on_eof(self):
        guest,host,pad,failures,thread=self.fixture();thread.start()
        self.assertEqual(host.recv(128),b'LH_INPUT_READY\n')
        host.sendall(b'{"touches":[[1,0.2,')
        host.settimeout(.05)
        with self.assertRaises(socket.timeout):host.recv(128)
        self.assertFalse(pad.slots)
        host.settimeout(3);host.sendall(b'0.3],[2,0.7,0.8]],"buttons":1}\n')
        self.assertEqual(host.recv(128),b'LH_INPUT_APPLIED\n')
        self.assertEqual(set(pad.slots),{1,2});self.assertEqual(pad.buttons,1)
        host.sendall(b'{"touches":[[3,2,0]]}\n')
        deadline=time.monotonic()+1
        while pad.slots and time.monotonic()<deadline:time.sleep(.005)
        self.assertFalse(pad.slots);self.assertEqual(pad.buttons,0)
        host.settimeout(.05)
        with self.assertRaises(socket.timeout):host.recv(128)
        host.settimeout(3)
        host.sendall(b'{"touches":[[4,0.5,0.5]],"buttons":0}\n')
        self.assertEqual(host.recv(128),b'LH_INPUT_APPLIED\n')
        host.shutdown(socket.SHUT_WR);thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(failures,[])
        self.assertFalse(pad.slots);self.assertEqual(pad.buttons,0)

    def test_backpressure_waits_for_writability_and_still_releases_contacts(self):
        guest,host,pad,failures,thread=self.fixture()
        # Saturate only guest->host, leaving incoming touches readable.
        while True:
            try:guest.send(bytes(16384))
            except BlockingIOError:break
        waits=[];real_select=select.select
        def wait(read,write,error,timeout):
            waits.append((bool(write),timeout));return real_select(read,write,error,timeout)
        with patch.object(trackpad.select,'select',wait):
            thread.start()
            host.sendall(b'{"touches":[[1,0.5,0.5]],"buttons":1}\n')
            deadline=time.monotonic()+2
            while not pad.slots and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(pad.slots)
            began=time.monotonic()
            while pad.slots and time.monotonic()-began<1.5:time.sleep(.01)
            self.assertFalse(pad.slots);self.assertEqual(pad.buttons,0)
            self.assertLessEqual(len(waits),4,'Blocked feedback caused a busy loop')
            self.assertTrue(any(write and timeout is None for write,timeout in waits))
            # Draining the blocked direction resumes READY without an idle poll.
            data=b''
            while b'LH_INPUT_READY\n' not in data:data=(data+host.recv(65536))[-128:]
            host.shutdown(socket.SHUT_WR);thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(failures,[])

    def test_transport_failures_release_state_and_close_port_if_uinput_creation_fails(self):
        pad=trackpad.Pad(Recorder());pad.frame([[1,.5,.5]],1)
        with patch.object(trackpad.os,'write',return_value=2):
            with self.assertRaises(OSError) as error:trackpad.serve(7,pad)
        self.assertEqual(error.exception.errno,errno.EIO)
        self.assertFalse(pad.slots);self.assertEqual(pad.buttons,0)
        guest,host=socket.socketpair()
        try:
            fd=os.dup(guest.fileno())
            with patch.object(trackpad.os,'open',return_value=fd), \
                    patch.object(trackpad,'create',side_effect=OSError('uinput unavailable')), \
                    patch.object(trackpad.time,'sleep',side_effect=KeyboardInterrupt),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):trackpad.main()
            with self.assertRaises(OSError):os.fstat(fd)
        finally:guest.close();host.close()


if __name__=='__main__':unittest.main()
