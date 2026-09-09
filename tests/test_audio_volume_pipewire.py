"""Real PipeWire gain test in a private daemon, with no hardware or session manager.

The only stream is generated constant PCM, held in pipes/RAM. Never opens the
user's PipeWire socket, audio hardware or microphone; temporary files are removed.
"""
import array
import json
import os
from pathlib import Path
import select
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
import unittest

CONFIG = '''context.properties = { core.daemon = true core.name = pipewire-0 default.clock.rate = 48000 default.clock.quantum = 256 }
context.spa-libs = { audio.convert.* = audioconvert/libspa-audioconvert support.* = support/libspa-support }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-spa-node-factory }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-adapter }
 { name = libpipewire-module-link-factory }
 { name = libpipewire-module-access args = { access.legacy = true } }
]
context.objects = [ { factory = spa-node-factory args = { factory.name = support.node.driver node.name = Dummy-Driver node.group = pipewire.dummy node.driver = true priority.driver = 20000 } } ]
'''


@unittest.skipUnless(all(shutil.which(name) for name in ('pipewire', 'pw-loopback', 'pw-cat', 'pw-cli', 'pw-link', 'pw-dump', 'wpctl')), 'PipeWire CLI required')
class PipeWireVolumeIntegration(unittest.TestCase):
    def test_displayed_volume_and_actual_sample_gain(self):
        with tempfile.TemporaryDirectory(prefix='ashacky-volume-test-') as temp:
            directory=Path(temp);(directory/'check.conf').write_text(CONFIG)
            env=os.environ|{'XDG_RUNTIME_DIR':temp,'PIPEWIRE_RUNTIME_DIR':temp,'PIPEWIRE_REMOTE':'pipewire-0','PIPEWIRE_CONFIG_DIR':temp,'PIPEWIRE_CONFIG_NAME':'check.conf','PIPEWIRE_DEBUG':'3'}
            children=[]
            log=(directory/'log').open('wb')
            def start(args,**kw):
                p=subprocess.Popen(args,env=env,stderr=log,**kw);children.append(p);return p
            def run(*args):return subprocess.check_output(args,env=env,stderr=log,timeout=5)
            try:
                server=start(['pipewire'],stdout=log)
                deadline=time.monotonic()+5
                while not (directory/'pipewire-0').exists():
                    assert server.poll() is None
                    if time.monotonic()>deadline:raise RuntimeError('Private PipeWire did not start')
                    time.sleep(.02)
                # Clients use their normal shipped client.conf, only the server config is private.
                env.pop('PIPEWIRE_CONFIG_NAME');env.pop('PIPEWIRE_CONFIG_DIR')
                public={'node.name':'volume-check','media.class':'Audio/Sink','node.autoconnect':False,'audio.position':['FL','FR']}
                internal={'node.name':'volume-discard','node.autoconnect':False,'audio.position':['FL','FR']}
                loop=start(['pw-loopback','--name','volume-check','--channels','2','--capture-props',json.dumps(public),'--playback-props',json.dumps(internal)],stdout=log)
                deadline=time.monotonic()+5
                while True:
                    objects=json.loads(run('pw-dump'))
                    nodes={o.get('info',{}).get('props',{}).get('node.name'):o for o in objects if o.get('type')=='PipeWire:Interface:Node'}
                    if 'volume-check' in nodes:break
                    if time.monotonic()>deadline:raise RuntimeError('Private sink did not appear')
                    time.sleep(.02)
                ident=nodes['volume-check']['id']
                run('pw-cli','set-param',str(ident),'Props','{ channelVolumes: [ 0.125 0.125 ] softVolumes: [ 1.0 1.0 ] mute: false softMute: false }')
                time.sleep(.05)
                node=next(o for o in json.loads(run('pw-dump')) if o['id']==ident)
                props=node['info']['params']['Props'][0]
                assert props['channelVolumes']==[.125,.125] and props['softVolumes']==[1.,1.]
                assert run('wpctl','get-volume',str(ident)).decode().strip() == 'Volume: 0.50'
                # Synthetic streams only, all links stay on this private server.
                player=start(['pw-cat','-p','--raw','--target','0','--format','f32','--rate','48000','--channels','2','--properties','{ node.name = volume-generator }','-'],stdin=subprocess.PIPE,stdout=log)
                recorder=start(['pw-cat','-r','--raw','--target','0','--format','f32','--rate','48000','--channels','2','--properties','{ node.name = volume-recorder }','-'],stdout=subprocess.PIPE)
                time.sleep(.15)
                objects=json.loads(run('pw-dump'))
                names={o.get('info',{}).get('props',{}).get('node.name'):o['id'] for o in objects if o.get('type')=='PipeWire:Interface:Node'}
                for name,direction in [('volume-check','Input'),('volume-discard','Output'),('volume-generator','Output'),('volume-recorder','Input')]:
                    run('pw-cli','set-param',str(names[name]),'PortConfig','{ direction: '+direction+' mode: dsp format: { mediaType: audio mediaSubtype: raw format: F32P rate: 48000 channels: 2 position: [ FL FR ] } }')
                for channel in ('FL','FR'):
                    run('pw-link','volume-generator:output_'+channel,'volume-check:playback_'+channel)
                    run('pw-link','volume-discard:output_'+channel,'volume-recorder:input_'+channel)
                def feed():
                    samples=array.array('f',[.1]*4096).tobytes()
                    try:
                        while player.poll() is None:
                            player.stdin.write(samples);player.stdin.flush()
                    except (BrokenPipeError,OSError):pass
                thread=threading.Thread(target=feed,daemon=True);thread.start()
                os.set_blocking(recorder.stdout.fileno(),False)
                def measure(value):
                    run('pw-cli','set-param',str(ident),'Props',value)
                    result=bytearray();start_time=time.monotonic()
                    while time.monotonic()-start_time<.7:
                        if select.select([recorder.stdout],[],[],.02)[0]:
                            data=os.read(recorder.stdout.fileno(),65536)
                            if time.monotonic()-start_time>.35:result.extend(data)
                    samples=array.array('f');samples.frombytes(result[:len(result)//4*4])
                    assert len(samples)>1000,'Private audio graph produced no samples'
                    return statistics.median(samples)
                unity=measure('{ channelVolumes: [ .125 .125 ] softVolumes: [ 1 1 ] mute: false softMute: false }')
                attenuated=measure('{ channelVolumes: [ .125 .125 ] mute: false }')
                muted=measure('{ channelVolumes: [ .125 .125 ] softVolumes: [ 1 1 ] mute: true softMute: true }')
                assert abs(unity-.1)<.0001,(unity,attenuated,muted)
                assert abs(attenuated-.0125)<.0001,(unity,attenuated,muted)
                assert abs(muted)<.0001,(unity,attenuated,muted)

            finally:
                for p in reversed(children):
                    if p.poll() is None:p.terminate()
                for p in children:
                    try:p.wait(timeout=3)
                    except subprocess.TimeoutExpired:p.kill();p.wait()
                if 'thread' in locals():thread.join(timeout=2)
                for p in children:
                    for stream in (p.stdin,p.stdout):
                        if stream is not None:
                            try:stream.close()
                            except BrokenPipeError:pass
                log.close()
