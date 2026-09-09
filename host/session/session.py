#!/usr/bin/python3
"""One VM per dedicated login. QMP verifies shutdown before host power actions."""
import argparse, errno, fcntl, json, os, pathlib, pwd, select, signal, socket, subprocess as sp, time
APP=pathlib.Path(__file__).resolve().parents[2]/'build/host/Ashacky.app/Contents'
STOP=False

def rpc(config, action, *, timeout=85, **kw):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout); s.connect(config['socket']+('.supervisor' if action=='vm-stopped' else ''))
        s.sendall(json.dumps(dict(action=action,token=config['token'],**kw)).encode()+b'\n')
        return json.loads(s.makefile('rb').readline(16384))

def arguments(c, network_fds):
    runtime=pathlib.Path(c['runtime']); runtime.mkdir(mode=0o700,parents=True,exist_ok=True)
    a=[str(APP/'MacOS/qemu-system-aarch64'),'-L',str(APP/'Resources/qemu'),'-name',c.get('name','Debian'),'-uuid',c['uuid'],
       '-S','-boot','menu=off,splash-time=0','-machine','virt,gic-version=3','-accel','hvf,ipa-granule-size=0x1000','-cpu','host',
       '-smp',str(c.get('cpus',4)),'-m',str(c.get('memory',8192)),'-nodefaults','-vga','none']
    for i,(mac,fd) in enumerate(zip(c['macs'],network_fds)):
        a+=['-device',f'virtio-net-pci,mac={mac},netdev=net{i},addr=0x{i+1:x},romfile=', '-netdev',f'socket,id=net{i},fd={fd}']
    a+=['-device','virtio-ramfb-gl,addr=0x3,hostmem=8G,blob=true,venus=true,neptune=true,max_outputs=2,xres=2880,yres=1800',
        '-display','none','-spice',f'unix=on,addr={runtime}/spice.sock,disable-ticketing=on,gl=es,image-compression=off,playback-compression=off,streaming-video=off',
        '-drive',f'if=pflash,format=raw,unit=0,readonly=on,file={APP}/Resources/edk2-aarch64-code.fd',
        '-drive',f'if=pflash,format=qcow2,unit=1,file={c["vars"]}',
        '-audiodev','spice,id=audio0','-device','intel-hda,addr=0x4','-device','hda-duplex,audiodev=audio0',
        '-device','nec-usb-xhci,id=usb-bus,addr=0x5','-device','usb-tablet,bus=usb-bus.0',
        '-device','usb-mouse,bus=usb-bus.0','-device','usb-kbd,bus=usb-bus.0',
        '-device','qemu-xhci,id=usb-controller-0,addr=0x6',
        '-drive',f'if=none,id=disk,format=qcow2,file={c["disk"]},discard=unmap,detect-zeroes=unmap',
        '-device',f'virtio-blk-pci,addr=0x7,drive=disk,serial={c["diskSerial"]},bootindex=1',
        '-device','virtio-serial-pci,addr=0x8',
        '-chardev','spicevmc,id=vdagent,name=vdagent','-device','virtserialport,chardev=vdagent,name=com.redhat.spice.0',
        '-chardev',f'socket,id=qga,path={runtime}/qga.sock,server=on,wait=off','-device','virtserialport,chardev=qga,name=org.qemu.guest_agent.0',
        '-device','virtio-rng-pci,addr=0x9',
        '-fsdev',f'local,id=shared,path={c["shared"]},security_model=mapped-xattr',
        '-device','virtio-9p-pci,addr=0xa,fsdev=shared,mount_tag=shared','-serial',f'file:{runtime}/serial.log',
        '-qmp',f'unix:{runtime}/qmp.sock,server=on,wait=off']
    for index in range(4):
        a+=['-chardev',f"socket,id=usbredir{index},path={c['usbSocket']},reconnect-ms=1000",
            '-device',f'usb-redir,chardev=usbredir{index},id=usbredir{index},bus=usb-controller-0.0,port={index+1}']
    a+=['-chardev','spiceport,id=lhinput,name=org.linuxhost.input',
        '-device','virtserialport,chardev=lhinput,name=org.linuxhost.input']
    a+=['-chardev','spiceport,id=ashacky-status,name=org.ashacky.status',
        '-device','virtserialport,chardev=ashacky-status,name=org.ashacky.status']
    a+=['-object',f'memory-backend-file,id=video-frames,size=64M,mem-path={runtime}/video-frames.bin,share=on','-device','linuxhost-shmem,memdev=video-frames,addr=0xb']
    if c.get('provisioningISO'):
        a+=['-drive',f'if=none,id=ashacky-seed,format=raw,media=cdrom,readonly=on,file={c["provisioningISO"]}',
            '-device','usb-storage,drive=ashacky-seed,bus=usb-bus.0']
    return a

def main():
    global STOP,APP
    from user_services import UserServices
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--check',action='store_true');args=p.parse_args()
    print('Login supervisor entered at '+time.strftime('%Y-%m-%d %H:%M:%S'),flush=True)
    c=json.loads(pathlib.Path(args.config).read_text()); control=json.loads(pathlib.Path(c['control']).read_text())
    for name in ('disk','vars',*(['provisioningISO'] if c.get('provisioningISO') else [])):
        path=pathlib.Path(c[name]);st=path.stat()
        if not path.is_file() or path.is_symlink() or st.st_uid!=os.getuid(): raise RuntimeError('Invalid VM '+name)
    if c['userID'] != os.getuid() or os.getuid() == 0:
        raise RuntimeError('The configured dedicated account must run its own VM')
    import uuid, re
    uuid.UUID(c['uuid'])
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', c['diskSerial']):
        raise RuntimeError('Invalid virtual disk serial')
    if len(c['macs']) != 2 or len(c['networks']) != 2:
        raise RuntimeError('This device topology requires two network backends')
    for mac in c['macs']:
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', mac):
            raise RuntimeError('Invalid virtual NIC address')
    APP = pathlib.Path(c.get('appContents', str(APP)))
    if not APP.is_absolute():
        raise RuntimeError('appContents must be absolute')
    if args.check:
        print('Standalone VM configuration and identity verified');return
    deadline=time.monotonic()+90
    while int(sp.check_output(['/usr/bin/stat','-f','%u','/dev/console']))!=os.getuid():
        if time.monotonic()>deadline:raise RuntimeError('Linux did not become the active console session')
        time.sleep(.5)
    os.umask(0o077); runtime=pathlib.Path(c['runtime']);runtime.mkdir(parents=True,exist_ok=True)
    lock=open(runtime/'session.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    # Exclude any existing use of this disk before spawning a second hypervisor.
    opened=sp.run(['/usr/sbin/lsof','-t',c['disk']],capture_output=True,text=True)
    if opened.stdout.strip(): raise RuntimeError('The VM disk is already open; close its current VM before starting standalone')
    env=dict(os.environ,ANGLE_DEFAULT_PLATFORM='metal',RENDER_SERVER_EXEC_PATH=str(APP/'MacOS/virgl_render_server'),VK_DRIVER_FILES=str(APP/'Resources/MoltenVK_icd.json'),GST_PLUGIN_PATH=str(APP/'Resources/gstreamer-1.0'),GST_PLUGIN_SYSTEM_PATH='',GST_REGISTRY=str(runtime/'gst-registry.bin'),GST_REGISTRY_FORK='no',LINUXHOST_SPICE_SOCKET=str(runtime/'spice.sock'),LINUXHOST_TITLE=c.get('name','Debian'),LINUXHOST_CONTROL_CONFIG=c['control'],ASHACKY_PRIVATE_DIRECTORY=str(pathlib.Path(c['control']).parent))
    if c.get('test'): env['LINUXHOST_DIAGNOSTICS']=str(runtime)
    for key in ('DYLD_LIBRARY_PATH','DYLD_FRAMEWORK_PATH','DYLD_FALLBACK_FRAMEWORK_PATH'): env.pop(key,None)
    env['DYLD_FRAMEWORK_PATH']=str(APP/'Frameworks')
    env['DYLD_FALLBACK_FRAMEWORK_PATH']=str(APP/'Frameworks')+':/System/Library/Frameworks'
    for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig,lambda *_:globals().__setitem__('STOP',True))
    source=pathlib.Path(__file__).resolve().parents[2]
    services=UserServices({
        'control-forward':[os.sys.executable,str(source/'host/session/control-forward.py')],
        'device-forwards':[os.sys.executable,str(source/'host/transport/probe-device-service.py')],
        'h264':[str(APP/'MacOS/vtremoted'),'--listen',c['videoBindAddress']+':5557'],
    },env,runtime.parent/'logs')
    networks=[];children=[];qemu=None;view=None;clean=False;buffer=b''
    try:
        # LaunchAgents and root network helpers have no guaranteed startup order.
        # Wait only before boot; intentional guest shutdown still never restarts it.
        deadline=time.monotonic()+90
        for path in c['networks']:
            while True:
                if STOP:raise RuntimeError('Login startup cancelled')
                s=socket.socket(socket.AF_UNIX);s.settimeout(3)
                try:s.connect(path);networks.append(s);break
                except OSError as error:
                    s.close()
                    if error.errno in (errno.EACCES, errno.EPERM):
                        raise RuntimeError('Network socket access denied: '+path+'; check ownership, directory traversal permissions and account groups') from error
                    if time.monotonic()>deadline:raise RuntimeError('Network helper did not become ready: '+path)
                    time.sleep(.5)
        print('Network helpers ready at '+time.strftime('%Y-%m-%d %H:%M:%S'),flush=True)
        services.start_all()
        # A fresh, private mapping for this QEMU session; no pixel traffic over TCP.
        fd=os.open(runtime/'video-frames.bin',os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        try:os.ftruncate(fd,64*1024*1024)
        finally:os.close(fd)
        video_env=dict(env,LH_VIDEO_SHMEM=str(runtime/'video-frames.bin'),LH_VIDEO_BIND=c['videoBindAddress'])
        with open(runtime/'video-shared.log','ab',buffering=0) as log:
            video=sp.Popen([str(APP/'MacOS/LinuxHostVideoShared')],env=video_env,stdout=log,stderr=log);children.append(video)
        with open(runtime/'qemu.log','ab',buffering=0) as log:
            qemu=sp.Popen(arguments(c,[s.fileno() for s in networks]),pass_fds=[s.fileno() for s in networks],env=env,stdout=log,stderr=log);children.append(qemu)
        for s in networks:s.close()
        deadline=time.monotonic()+30
        qmp=socket.socket(socket.AF_UNIX);qmp.settimeout(2)
        while True:
            try:qmp.connect(str(runtime/'qmp.sock'));break
            except OSError:
                if qemu.poll() is not None or time.monotonic()>deadline:raise RuntimeError('QEMU failed to start; see qemu.log')
                time.sleep(.2)
        def command(name):qmp.sendall(json.dumps({'execute':name}).encode()+b'\n')
        # Read the greeting before negotiating QMP.
        qmp.recv(65536);command('qmp_capabilities');qmp.recv(65536)
        with open(runtime/'display.log','ab',buffering=0) as log:
            view=sp.Popen([str(APP/'MacOS/AshackyLauncher')],env=env,stdout=log,stderr=log);children.append(view)
        # Control and lock observation are linked into the app. Do not boot the
        # guest until its authenticated control endpoint is ready.
        deadline=time.monotonic()+30
        while True:
            try:
                if rpc(control,'status',timeout=2).get('bundleID')=='local.ashacky.host':break
            except (OSError, ValueError):pass
            if STOP or view.poll() is not None or time.monotonic()>deadline:
                raise RuntimeError('Ashacky session services did not become ready; see display.log')
            time.sleep(.2)
        command('cont');print('QMP cont sent at '+time.strftime('%Y-%m-%d %H:%M:%S'),flush=True);shutdown_deadline=None;retries=[]
        print('Standalone VM started at '+time.strftime('%Y-%m-%d %H:%M:%S')+' supervisor='+str(os.getpid()),flush=True)
        video_retries=[]
        while qemu.poll() is None:
            if not STOP and shutdown_deadline is None:services.poll()
            if video.poll() is not None and not STOP and shutdown_deadline is None:
                video_retries=[t for t in video_retries if time.monotonic()-t<60]
                if len(video_retries)<3:
                    video_retries.append(time.monotonic())
                    with open(runtime/'video-shared.log','ab',buffering=0) as log:
                        video=sp.Popen([str(APP/'MacOS/LinuxHostVideoShared')],env=video_env,stdout=log,stderr=log);children.append(video)
            if STOP and shutdown_deadline is None:command('system_powerdown');shutdown_deadline=time.monotonic()+60
            if view.poll() is not None and shutdown_deadline is None:
                if view.returncode==0:command('system_powerdown');shutdown_deadline=time.monotonic()+60
                else:
                    retries=[t for t in retries if time.monotonic()-t<60]
                    if len(retries)>=3:raise RuntimeError('Display repeatedly crashed; stopping guest')
                    retries.append(time.monotonic())
                    with open(runtime/'display.log','ab',buffering=0) as log:view=sp.Popen([str(APP/'MacOS/AshackyLauncher')],env=env,stdout=log,stderr=log);children.append(view)
            if shutdown_deadline and time.monotonic()>shutdown_deadline:raise RuntimeError('Guest shutdown timed out')
            if select.select([qmp],[],[],.3)[0]:
                chunk=qmp.recv(65536)
                if not chunk:break
                buffer+=chunk
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1)
                    event=json.loads(line)
                    if event.get('event')=='SHUTDOWN':
                        clean=event.get('data',{}).get('guest') is True
                        print('QMP shutdown: '+json.dumps(event.get('data')),flush=True)
        qemu.wait(timeout=10)
        # Only QMP-confirmed guest shutdown can complete an armed host operation.
        if clean:
            print('Guest stopped cleanly',flush=True)
            # Closing the app also shuts down the guest, but cannot carry an
            # armed host action: that state belongs to the app process.
            if view.poll() is None:
                print(rpc(control,'vm-stopped',clean=True),flush=True);time.sleep(3)
        elif qemu.returncode:raise RuntimeError('QEMU exited unexpectedly')
    finally:
        if qemu and qemu.poll() is None:
            try:command('system_powerdown');qemu.wait(timeout=30)
            except Exception:qemu.terminate()
        for child in reversed(children):
            if child.poll() is None:child.terminate()
        for child in children:
            try:child.wait(timeout=5)
            except sp.TimeoutExpired:child.kill()
        services.stop()
        for s in networks:s.close()
        print('Standalone session stopped',flush=True)
if __name__=='__main__':main()
