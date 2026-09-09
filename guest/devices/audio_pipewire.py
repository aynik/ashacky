#!/usr/bin/env python3
"""Host audio choices as PipeWire sinks/sources, using the shared SPICE transport.

Global endpoint selection only. These nodes do not provide independent physical
routing for concurrent applications. Host audio remains authoritative on hotplug.
"""
import argparse
import hashlib
import json
import os
import subprocess
import time
from bluetooth_management import request
from audio_events import AudioEvents

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


class AudioBridge:
    def __init__(self):
        self.children = {}
        self.child_watches = {}
        self.observed = {}
        self.observed_levels = {}
        self.pending_defaults = {}
        self.original = None
        self.state = None
        self.events = AudioEvents(self.reconcile)

    def child_exited(self, pid, status, name, process):
        process.returncode = os.waitstatus_to_exitcode(status)
        self.child_watches.pop(name, None)
        if self.children.get(name) is process:
            self.children.pop(name)
            self.events.retry()

    def stop_child(self, name):
        watch = self.child_watches.pop(name, None)
        if watch:
            self.events.GLib.source_remove(watch)
        stop(self.children.pop(name))

    def reconcile(self):
        if not self.events.graph.ready:
            return
        try:
            objects = self.events.snapshot()
            if self.original is None:
                self.original = defaults(objects)
            if self.events.host_dirty or self.state is None:
                self.state = request({'action': 'audio-devices'}, SOCKET)
                self.events.host_dirty = False
            state = self.state
            records = {node_name(d['uid'], direction): (d, direction)
                       for d in state['devices'] for direction in KIND if d[direction]}
            for name in list(self.children):
                if name not in records:
                    self.stop_child(name)
            for name, (device, direction) in records.items():
                if name not in self.children:
                    process = spawn(device, direction)
                    self.children[name] = process
                    self.child_watches[name] = self.events.GLib.child_watch_add(
                        self.events.GLib.PRIORITY_DEFAULT, process.pid, self.child_exited, name, process)
            current = defaults(objects)
            for direction, kind in KIND.items():
                key = 'default.configured.audio.' + kind
                selected = current.get(key)
                pending = self.pending_defaults.get(direction)
                if pending:
                    before, wanted, deadline = pending
                    if selected == before and time.monotonic() < deadline:
                        # A graph event can arrive before our metadata write is
                        # observed. Do not mistake the old default for user input.
                        continue
                    self.pending_defaults.pop(direction, None)
                if direction in self.observed and selected != self.observed[direction] and selected in records:
                    device, actual_direction = records[selected]
                    if actual_direction == direction:
                        state = request({'action': 'audio-select', 'direction': direction,
                                         'uid': device['uid']}, SOCKET)
                        self.state = state
                active = next((d for d in state['devices'] if d['default' + direction.title()]), None)
                if active:
                    wanted = node_name(active['uid'], direction)
                    if selected != wanted and set_default(wanted, objects):
                        self.pending_defaults[direction] = (selected, wanted, time.monotonic() + 2)
                        self.events.graph.dirty_metadata.update(self.events.graph.metadata)
                        self.events.later('defaults', 100, self.events.notify)
                        self.events.later('defaults-deadline', 2100, self.events.notify)
                        selected = wanted
                self.observed[direction] = selected
                if selected in records:
                    prepare_transport(selected, direction, objects, self.observed_levels)
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
            print('Audio synchronization unavailable:', type(error).__name__, flush=True)
            for name in list(self.children):
                self.stop_child(name)
            self.state = None
            self.observed.clear()
            self.observed_levels.clear()
            self.pending_defaults.clear()
            self.events.retry()

    def run(self, duration):
        try:
            self.events.run(duration)
        finally:
            self.events.close()
            try:
                if self.original is not None:
                    objects = dump()
                    current = defaults(objects)
                    for direction, kind in KIND.items():
                        key = 'default.configured.audio.' + kind
                        if (current.get(key) or '').startswith('linuxhost.'):
                            set_default(self.original.get(key) or TRANSPORT[direction], objects)
            finally:
                for name in list(self.children):
                    self.stop_child(name)


def run(duration):
    AudioBridge().run(duration)


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
    run(args.duration)
