#!/usr/bin/python3
"""Host touch frames -> a standard Linux type-B multitouch touchpad."""
import errno, json, os, select, time
from evdev import UInput, AbsInfo, ecodes as e
PORT='/dev/virtio-ports/org.linuxhost.input'
def create():
    def axis(maximum,res=0): return AbsInfo(0,0,maximum,0,0,res)
    caps={e.EV_KEY:[e.BTN_LEFT,e.BTN_RIGHT,e.BTN_MIDDLE,e.BTN_TOUCH,e.BTN_TOOL_FINGER,e.BTN_TOOL_DOUBLETAP,e.BTN_TOOL_TRIPLETAP,e.BTN_TOOL_QUADTAP,e.BTN_TOOL_QUINTTAP],e.EV_ABS:[(e.ABS_X,axis(13000,100)),(e.ABS_Y,axis(8000,100)),(e.ABS_MT_SLOT,axis(9)),(e.ABS_MT_TRACKING_ID,axis(65535)),(e.ABS_MT_POSITION_X,axis(13000,100)),(e.ABS_MT_POSITION_Y,axis(8000,100))]}
    return UInput(caps,name='LinuxHost Multitouch Trackpad',vendor=0x1209,product=0x4c48,version=1,bustype=e.BUS_VIRTUAL,input_props=[e.INPUT_PROP_POINTER])
def validate(v):
    if not isinstance(v,dict): raise ValueError('frame')
    points=v.get('touches')
    if not isinstance(points,list) or len(points)>10: raise ValueError('touch count')
    seen=set()
    for p in points:
        if not isinstance(p,list) or len(p)!=3: raise ValueError('point')
        i,x,y=p
        if type(i)!=int or not 0<=i<65536 or i in seen: raise ValueError('identity')
        if not all(type(n) in (int,float) and 0<=n<=1 for n in (x,y)): raise ValueError('coordinate')
        seen.add(i)
    buttons=v.get('buttons',0)
    if type(buttons)!=int or not 0<=buttons<=7: raise ValueError('buttons')
    return points,buttons
class Pad:
    def __init__(self,ui): self.ui=ui;self.slots={};self.buttons=0
    def frame(self,points,buttons):
        u=self.ui; ids={p[0] for p in points}
        for ident in list(self.slots):
            if ident not in ids:
                u.write(e.EV_ABS,e.ABS_MT_SLOT,self.slots.pop(ident));u.write(e.EV_ABS,e.ABS_MT_TRACKING_ID,-1)
        for ident,x,y in points:
            new=ident not in self.slots
            if new:self.slots[ident]=next(n for n in range(10) if n not in self.slots.values())
            u.write(e.EV_ABS,e.ABS_MT_SLOT,self.slots[ident])
            if new:u.write(e.EV_ABS,e.ABS_MT_TRACKING_ID,ident)
            u.write(e.EV_ABS,e.ABS_MT_POSITION_X,round(x*13000));u.write(e.EV_ABS,e.ABS_MT_POSITION_Y,round(y*8000))
        if points:
            u.write(e.EV_ABS,e.ABS_X,round(points[0][1]*13000));u.write(e.EV_ABS,e.ABS_Y,round(points[0][2]*8000))
        u.write(e.EV_KEY,e.BTN_TOUCH,int(bool(points)))
        for count,code in enumerate([e.BTN_TOOL_FINGER,e.BTN_TOOL_DOUBLETAP,e.BTN_TOOL_TRIPLETAP,e.BTN_TOOL_QUADTAP,e.BTN_TOOL_QUINTTAP],1):u.write(e.EV_KEY,code,int(min(5,len(points))==count))
        for bit,code in enumerate([e.BTN_LEFT,e.BTN_RIGHT,e.BTN_MIDDLE]):u.write(e.EV_KEY,code,int(bool(buttons&(1<<bit))))
        u.syn();self.buttons=buttons

def reply(fd,message):
    if os.write(fd,message)!=len(message):
        raise OSError(errno.EIO,'Incomplete trackpad acknowledgement')

def serve(fd,pad):
    """Wait for input, heartbeat/held-contact deadlines or reply writability."""
    buf=b'';last=time.monotonic();next_ready=last;blocked=False
    try:
        while True:
            now=time.monotonic()
            if (pad.slots or pad.buttons) and now-last>=1:
                pad.frame([],0)
            if now>=next_ready and not blocked:
                try:
                    reply(fd,b'LH_INPUT_READY\n');next_ready=time.monotonic()+1
                except BlockingIOError:
                    blocked=True
            deadlines=[] if blocked else [next_ready]
            if pad.slots or pad.buttons:deadlines.append(last+1)
            timeout=max(0,min(deadlines)-time.monotonic()) if deadlines else None
            readable,writable,_=select.select([fd],[fd] if blocked else [],[],timeout)
            if writable:blocked=False
            if not readable:continue
            try:data=os.read(fd,8192)
            except BlockingIOError:continue
            if not data:return
            buf+=data
            if len(buf)>65536:raise ValueError('oversized input')
            while b'\n' in buf:
                line,buf=buf.split(b'\n',1)
                try:
                    points,buttons=validate(json.loads(line));pad.frame(points,buttons);last=time.monotonic()
                    # Only a validated frame submitted to uinput permits the
                    # host to suppress its ordinary pointer path.
                    if points:
                        try:reply(fd,b'LH_INPUT_APPLIED\n')
                        except BlockingIOError:pass
                except (ValueError,TypeError,KeyError):pad.frame([],0)
    finally:pad.frame([],0)

def main():
    while True:
        try:
            fd=os.open(PORT,os.O_RDWR|os.O_NONBLOCK)
            try:
                with create() as ui:serve(fd,Pad(ui))
            finally:os.close(fd)
        except (OSError,ValueError) as error:print(str(error),flush=True)
        time.sleep(1)
if __name__=='__main__':main()
