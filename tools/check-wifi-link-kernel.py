#!/usr/bin/env python3
"""Verify Wi-Fi link reports on a separate wiphy, dummy NIC and network namespace."""
import errno,fcntl,json,os,socket,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'guest/devices'))
import wifi_bridge as bridge
import wifi_link as link
from wifi_scan import BSS

import argparse,hashlib
parser=argparse.ArgumentParser(description="Build an isolated Wi-Fi kernel fixture; --run executes it with root in a fresh network namespace.")
parser.add_argument('--run',action='store_true')
parser.add_argument('--namespace-child',action='store_true',help=argparse.SUPPRESS)
args=parser.parse_args()
build=ROOT/'build/wifi-link-kernel-check'
def fixture_source():
    value=(ROOT/'guest/drivers/wifi/linuxhost_wifi.c').read_text()
    edits={
      '#include <linux/if_arp.h>':'#include <linux/if_arp.h>\n#include <linux/nsproxy.h>',
      'if (!radio) return -ENOMEM;':'if (!radio) return -ENOMEM;\n\twiphy_net_set(radio, current->nsproxy->net_ns);',
      '\"lhwifi%d\"':'\"lhcheck%d\"',
      'if (!p->netdev) { error = -ENOMEM; goto free_radio; }':'if (!p->netdev) { error = -ENOMEM; goto free_radio; }\n\tdev_net_set(p->netdev, wiphy_net(radio));',
      '.name = \"linuxhost-wifi\"':'.name = \"ashacky-wifi-check\"',
      'dev_get_by_name(&init_net, lowerdev)':'dev_get_by_name(wiphy_net(radio), lowerdev)',
    }
    for before,after in edits.items():
        assert value.count(before)==1,'Kernel fixture adaptation needs review'
        value=value.replace(before,after)
    return value
if not args.namespace_child:
    if args.run:
        assert os.geteuid()==0,'--run needs root; build first as the ordinary user'
        assert not Path('/sys/module/ashacky_wifi_check').exists(),'A kernel fixture is already loaded'
        assert (build/'ashacky_wifi_check.c').read_text()==fixture_source(),'Rebuild the kernel fixture from current source'
        recorded=json.loads((build/'build.json').read_text())
        for name,digest in recorded.items():
            assert hashlib.sha256((build/name).read_bytes()).hexdigest()==digest,'Kernel fixture build changed'
        subprocess.run(['unshare','--net',sys.executable,str(Path(__file__).resolve()),'--namespace-child'],check=True)
    else:
        assert os.geteuid()!=0,'Build the fixture as the ordinary user, then use --run with root'
        build.mkdir(parents=True,exist_ok=True)
        (build/'ashacky_wifi_check.c').write_text(fixture_source())
        (build/'Makefile').write_text('obj-m += ashacky_wifi_check.o\n')
        names=['NL80211_CMD_CONNECT','NL80211_CMD_DISCONNECT','NL80211_CMD_GET_WIPHY','NL80211_ATTR_IFINDEX','NL80211_ATTR_SSID','NL80211_ATTR_MAC','NL80211_ATTR_MAC_HINT','NL80211_ATTR_AUTH_TYPE','NL80211_ATTR_WPA_VERSIONS','NL80211_ATTR_CIPHER_SUITE_GROUP','NL80211_ATTR_CIPHER_SUITES_PAIRWISE','NL80211_ATTR_AKM_SUITES','NL80211_ATTR_PMK','NL80211_ATTR_PRIVACY','NL80211_ATTR_REASON_CODE','NL80211_ATTR_ROAM_SUPPORT']
        source='#include <stdio.h>\n#include <linux/nl80211.h>\nint main(void) { puts("{");\n'
        source+='\n'.join('printf("\\\"'+n+'\\\":%d,\\n", '+n+');' for n in names)
        source+='\nputs("\\\"end\\\":0}"); return 0; }\n'
        (build/'constants.c').write_text(source)
        subprocess.run([os.environ.get('CC','cc'),str(build/'constants.c'),'-o',str(build/'constants')],check=True)
        subprocess.run(['make','-C','/lib/modules/'+os.uname().release+'/build','M='+str(build),'modules'],check=True)
        record={n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in ['ashacky_wifi_check.c','ashacky_wifi_check.ko','constants']}
        (build/'build.json').write_text(json.dumps(record,indent=2)+'\n')
        print('Kernel fixture built; no module was loaded. Use --run with root for the isolated check.')
    raise SystemExit
assert os.geteuid()==0
assert os.readlink('/proc/self/ns/net')!=os.readlink('/proc/1/ns/net'),'Run in a fresh network namespace'
build=ROOT/'build/wifi-link-kernel-check'
C=json.loads(subprocess.check_output([str(build/'constants')]))
def run(*args):return subprocess.run(args,check=True,capture_output=True,timeout=10)
def nla(kind,data):
    size=len(data)+4
    return struct.pack('HH',size,kind)+data+bytes((-size)%4)
def attrs(data):
    out={};offset=0
    while offset+4<=len(data):
        size,kind=struct.unpack_from('HH',data,offset)
        assert size>=4 and offset+size<=len(data)
        out[kind&0x3fff]=data[offset+4:offset+size];offset+=(size+3)&~3
    return out
sock=socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,16);sock.bind((0,0));sock.settimeout(4)
serial=0
def nl(family,command,values,want=False):
    global serial
    serial+=1
    body=struct.pack('BBH',command,1,0)+b''.join(nla(k,v) for k,v in values)
    sock.sendto(struct.pack('IHHII',len(body)+16,family,5,serial,0)+body,(0,0))
    while True:
        data=sock.recv(65536);offset=0
        while offset+16<=len(data):
            size,kind,flags,seq,pid=struct.unpack_from('IHHII',data,offset)
            payload=data[offset+16:offset+size];offset+=(size+3)&~3
            if seq!=serial:continue
            if kind==2:
                error=struct.unpack_from('i',payload)[0]
                if error:raise OSError(-error,'Netlink test request failed')
                if not want:return
            elif kind==family and want:return attrs(payload[4:])
family=struct.unpack('H',nl(16,3,[(2,b'nl80211\0')],True)[1])[0]
def u32(name,value):return C[name],struct.pack('I',value)
run('ip','link','add','ashcheck0','type','dummy');run('ip','link','set','ashcheck0','up')
module=build/'ashacky_wifi_check.ko';device=None;loaded=False
SSID=b'Ashacky kernel check';A=bytes.fromhex('020000000001');B=bytes.fromhex('020000000002')
rsn=b'\x01\x00'+link.CCMP+b'\x01\x00'+link.CCMP+b'\x01\x00'+link.PSK+b'\x00\x00'
ies=bytes([0,len(SSID)])+SSID+bytes([48,len(rsn)])+rsn
first=BSS('00000000-0000-4000-8000-000000000001',SSID,A,2412,-5000,ies,False)
second=BSS('00000000-0000-4000-8000-000000000002',SSID,B,2412,-5500,ies,False)
evidence={}
try:
    run('insmod',str(module),'lowerdev=ashcheck0');loaded=True
    run('ip','link','set','lhcheck0','up')
    index=socket.if_nametoindex('lhcheck0')
    phy=nl(family,C['NL80211_CMD_GET_WIPHY'],[u32('NL80211_ATTR_IFINDEX',index)],True)
    assert C['NL80211_ATTR_ROAM_SUPPORT'] in phy
    evidence['roamCapabilityAdvertised']=True
    device=open('/dev/ashacky-wifi-check','wb',buffering=0)
    follower=link.LinkFollower(device)
    cap=bytearray(4);fcntl.ioctl(device,bridge.GET_CAPABILITIES,cap)
    assert struct.unpack('I',cap)[0]==3
    def connect(pin=None):
        values=[u32('NL80211_ATTR_IFINDEX',index),(C['NL80211_ATTR_SSID'],SSID),
            u32('NL80211_ATTR_AUTH_TYPE',0),u32('NL80211_ATTR_WPA_VERSIONS',2),
            u32('NL80211_ATTR_CIPHER_SUITE_GROUP',0x000fac04),u32('NL80211_ATTR_CIPHER_SUITES_PAIRWISE',0x000fac04),
            u32('NL80211_ATTR_AKM_SUITES',0x000fac02),(C['NL80211_ATTR_PMK'],bytes(range(32))),
            (C['NL80211_ATTR_PRIVACY'],b''),(C['NL80211_ATTR_MAC_HINT'],B)]
        if pin is not None:values.append((C['NL80211_ATTR_MAC'],pin))
        nl(family,C['NL80211_CMD_CONNECT'],values)
        data=bytearray(bridge.CONNECTION.size);fcntl.ioctl(device,bridge.GET_CONNECTION,data)
        number,operation,length,keys,reserved,bssid,ssid,key=bridge.CONNECTION.unpack(data)
        assert operation==1 and bssid==(pin or bytes(6)) and keys==32 and ssid[:length]==SSID
        data[:]=bytes(len(data));key=None
        return number
    def complete(number,address=A,status=0):
        fcntl.ioctl(device,bridge.CONNECTION_RESULT,struct.pack('<IHH6s2x',number,status,0,address))
    def rejected(call,code):
        try:call()
        except OSError as error:assert error.errno==code,(error.errno,code)
        else:raise AssertionError('Invalid kernel report accepted')
    sequence=connect()
    assert follower.snapshot()[1]&link.BUSY
    rejected(lambda:link.report(device,sequence+1,2,first),errno.ESTALE)
    link.report(device,sequence,2,first);complete(sequence)
    assert follower.snapshot()==(sequence,link.CONNECTED|link.PRIVATE,A,SSID)
    evidence['hintIsNotAnExplicitPin']=True
    # Wrong-generation, wrong-network, malformed IE and security downgrade.
    rejected(lambda:link.report(device,sequence+1,1,second),errno.ESTALE)
    from dataclasses import replace
    rejected(lambda:link.report(device,sequence,1,replace(second,ssid=b'Other')),errno.EINVAL)
    rejected(lambda:link.report(device,sequence,1,replace(second,information_elements=ies[:-1])),errno.EINVAL)
    rejected(lambda:link.report(device,sequence,1,replace(second,open=True)),errno.EINVAL)
    rejected(lambda:link.report(device,sequence,1,replace(second,information_elements=ies.replace(link.PSK,b'\x00\x0f\xac\x01'))),errno.EINVAL)
    assert follower.snapshot()[2]==A
    link.report(device,sequence,1,second)
    assert follower.snapshot()[2]==B
    link.report(device,sequence,1,second)  # Duplicate state must be harmless.
    evidence['roamAndDuplicateReport']=True
    link.report(device,sequence,0)
    assert not follower.snapshot()[1]&link.CONNECTED
    time.sleep(.1)  # Let queued cfg80211 events finish before a new request.
    sequence2=connect(A)
    rejected(lambda:link.report(device,sequence2,2,second),errno.EINVAL)
    rejected(lambda:complete(sequence2,B),errno.EINVAL)
    link.report(device,sequence2,2,first);complete(sequence2)
    assert follower.snapshot()[1]&link.PINNED
    rejected(lambda:link.report(device,sequence2,1,second),errno.EINVAL)
    rejected(lambda:link.report(device,sequence,0),errno.ESTALE)
    assert follower.snapshot()[2]==A
    evidence['explicitPinAndStaleDisconnectRejected']=True
    link.report(device,sequence2,0)
    evidence['invalidSecurityIdentityAndGenerationRejected']=True
    print(json.dumps(evidence,indent=2))
finally:
    if device:device.close()
    sock.close()
    if loaded:run('rmmod','ashacky_wifi_check')
    run('ip','link','delete','ashcheck0')
