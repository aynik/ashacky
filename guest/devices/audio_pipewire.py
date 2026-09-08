#!/usr/bin/env python3
"""Host audio choices as PipeWire sinks/sources, using the shared SPICE transport.

Global endpoint selection only. These nodes do not provide independent physical
routing for concurrent applications. Host audio remains authoritative on hotplug.
"""
import argparse
import hashlib
import json
import signal
import subprocess
import time
from bluetooth_management import request

SOCKET = '/run/linuxhost-devices/control.sock'
TRANSPORT = {'output': 'alsa_output.pci-0000_00_04.0.analog-stereo',
             'input': 'alsa_input.pci-0000_00_04.0.analog-stereo'}
KIND = {'output': 'sink', 'input': 'source'}


def dump():
    return json.loads(subprocess.check_output(['pw-dump'], timeout=5))


def defaults(objects):
    return {entry['key']: entry.get('value', {}).get('name')
            for obj in objects if obj.get('props', {}).get('metadata.name') == 'default'
            for entry in obj.get('metadata', []) if isinstance(entry.get('value'), dict)}


def nodes(objects):
    return {obj.get('info', {}).get('props', {}).get('node.name'): obj['id']
            for obj in objects if obj['type'] == 'PipeWire:Interface:Node'}


def set_default(name, objects):
    node_id = nodes(objects).get(name)
    if node_id is not None:
        subprocess.run(['wpctl', 'set-default', str(node_id)], check=True, timeout=5)
        return True
    return False


def node_name(uid, direction):
    return 'linuxhost.' + direction + '.' + hashlib.sha256(uid.encode()).hexdigest()[:20]


def prepare_transport(selected, direction, objects, observed_levels):
    """Make an audible public-volume change effective through the shared HDA path.

    Never raise a public volume or unmute a public source. In particular a
    restored zero/muted microphone remains zero/muted.
    """
    by_name = {o.get('info', {}).get('props', {}).get('node.name'): o
               for o in objects if o['type'] == 'PipeWire:Interface:Node'}
    public = by_name.get(selected)
    transport = by_name.get(TRANSPORT[direction])
    if not public or not transport:
        return
    props = next(iter(public.get('info', {}).get('params', {}).get('Props', [])), {})
    volumes = props.get('channelVolumes', [])
    state = (public['id'], transport['id'], tuple(volumes), props.get('mute', False))
    if observed_levels.get(direction) == state:
        return
    if volumes and max(volumes) > 0 and not props.get('mute', False):
        subprocess.run(['wpctl', 'set-volume', str(transport['id']), '1.0'], check=True, timeout=5)
        subprocess.run(['wpctl', 'set-mute', str(transport['id']), '0'], check=True, timeout=5)
    observed_levels[direction] = state


def spawn(device, direction):
    name = node_name(device['uid'], direction)
    public = {'node.name': name, 'node.description': device['name'],
              'media.class': 'Audio/Sink' if direction == 'output' else 'Audio/Source',
              'node.virtual': True, 'priority.session': 1}
    internal = {'node.name': name + '.transport', 'node.passive': True,
                'target.object': TRANSPORT[direction], 'stream.dont-remix': False,
                'node.dont-fallback': True}
    capture, playback = (public, internal) if direction == 'output' else (internal, public)
    return subprocess.Popen(['pw-loopback', '--name', name, '--channels', '2',
                             '--capture-props', json.dumps(capture),
                             '--playback-props', json.dumps(playback)],
                            stdout=subprocess.DEVNULL, stderr=None)


def run(duration):
    children = {}
    observed = {}
    observed_levels = {}
    original = defaults(dump())
    deadline = time.monotonic() + duration if duration else float("inf")
    try:
        while time.monotonic() < deadline:
            try:
                state = request({'action': 'audio-devices'}, SOCKET)
                records = {(node_name(d['uid'], direction)): (d, direction)
                           for d in state['devices'] for direction in KIND if d[direction]}
                for name in list(children):
                    if name not in records or children[name].poll() is not None:
                        stop(children.pop(name))
                for name, (device, direction) in records.items():
                    if name not in children:
                        children[name] = spawn(device, direction)
                objects = dump()
                current = defaults(objects)
                for direction, kind in KIND.items():
                    key = 'default.configured.audio.' + kind
                    selected = current.get(key)
                    # Only an observed user change requests a host change. Startup
                    # and host hotplug synchronize the UI from the actual host.
                    if direction in observed and selected != observed[direction] and selected in records:
                        device, actual_direction = records[selected]
                        if actual_direction == direction:
                            state = request({'action': 'audio-select', 'direction': direction,
                                             'uid': device['uid']}, SOCKET)
                    active = next((d for d in state['devices'] if d['default' + direction.title()]), None)
                    if active:
                        wanted = node_name(active['uid'], direction)
                        if selected != wanted and set_default(wanted, objects):
                            selected = wanted
                    observed[direction] = selected
                    if selected in records:
                        prepare_transport(selected, direction, objects, observed_levels)
            except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
                print('Audio synchronization unavailable:', type(error).__name__, flush=True)
                # Remove stale host choices when transport/authorization is lost.
                for process in children.values():
                    stop(process)
                children.clear()
                observed.clear()
                observed_levels.clear()
            time.sleep(1)
    finally:
        try:
            current_objects = dump()
            current = defaults(current_objects)
            for direction, kind in KIND.items():
                key = 'default.configured.audio.' + kind
                if (current.get(key) or '').startswith('linuxhost.'):
                    set_default(original.get(key) or TRANSPORT[direction], current_objects)
        finally:
            # A dead PipeWire server must not leave child processes behind.
            for process in children.values():
                stop(process)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=int, default=600)
    args = parser.parse_args()
    if not 0 <= args.duration <= 3600:
        parser.error('duration must be 0 (service) or 1..3600 seconds')
    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        run(args.duration)
    except KeyboardInterrupt:
        pass
