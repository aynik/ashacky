"""PipeWire change stream and host-audio invalidations for endpoint selection."""
import codecs
import json
import os
from pathlib import Path
import signal
import subprocess
import time

NODE = 'PipeWire:Interface:Node'
METADATA = 'PipeWire:Interface:Metadata'
STATUS = Path('/run/linuxhost/status.json')


class JSONStream:
    def __init__(self, limit=8 * 1024 * 1024):
        self.buffer = ''
        self.decoder = codecs.getincrementaldecoder('utf-8')()
        self.limit = limit

    def feed(self, data):
        self.buffer += self.decoder.decode(data)
        if len(self.buffer) > self.limit:
            raise ValueError('PipeWire monitor frame exceeds limit')
        result = []
        while self.buffer.strip():
            self.buffer = self.buffer.lstrip()
            try:
                value, end = json.JSONDecoder().raw_decode(self.buffer)
            except json.JSONDecodeError:
                break
            if not isinstance(value, list):
                raise ValueError('PipeWire monitor must emit arrays')
            result.append(value)
            self.buffer = self.buffer[end:]
        return result


class Graph:
    def __init__(self):
        self.nodes = {}
        self.metadata = {}
        self.dirty_metadata = set()
        self.ready = False

    @staticmethod
    def relevant(node):
        info = node.get('info') or {}
        props = info.get('props', {})
        if props.get('media.class') not in ('Audio/Sink', 'Audio/Source'):
            return None
        return (props.get('node.name'), props.get('media.class'),
                info.get('params', {}).get('Props', []))

    def update(self, batch):
        changed = not self.ready
        self.ready = True
        for entry in batch:
            if not isinstance(entry, dict) or type(entry.get('id')) is not int:
                raise ValueError('Invalid PipeWire monitor object')
            ident = entry['id']
            kind = entry.get('type')
            if kind == NODE:
                old = self.nodes.get(ident, {})
                changed |= self.relevant(old) != self.relevant(entry)
                self.nodes[ident] = entry
            elif kind == METADATA and entry.get('props', {}).get('metadata.name') == 'default':
                # pw-dump emits metadata deltas and does not identify deleted
                # keys. Fetch this one object on a metadata event, never on a
                # timer. The resulting temporary Client events are ignored.
                self.metadata[ident] = entry
                self.dirty_metadata.add(ident)
                changed = True
            elif kind is None and ('info' in entry or 'props' in entry):
                old = self.nodes.pop(ident, {})
                changed |= self.relevant(old) is not None
                if ident in self.metadata:
                    del self.metadata[ident]
                    self.dirty_metadata.discard(ident)
                    changed = True
        return changed

    def snapshot(self, query):
        for ident in list(self.dirty_metadata):
            entries = query(ident)
            current = next((e for e in entries if e.get('id') == ident and e.get('type') == METADATA), None)
            if current is None:
                self.metadata.pop(ident, None)
            else:
                self.metadata[ident] = current
            self.dirty_metadata.discard(ident)
        return list(self.nodes.values()) + list(self.metadata.values())


def host_revision(path=STATUS):
    try:
        age = max(0, time.time() - path.stat().st_mtime)
        value = json.loads(path.read_text())
        revision = value.get('audioRevision')
        if (value.get('ok') is True and value.get('statusTransport') == 'virtio-serial'
                and isinstance(revision, str) and 0 < len(revision) <= 128 and age < 25):
            return (revision, value.get('active'), value.get('wakeGeneration')), max(1, 25 - age)
    except (OSError, ValueError, AttributeError):
        pass
    return None, 0


class AudioEvents:
    def __init__(self, changed):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        try:
            from gi.repository import GLibUnix
            self.add_signal = GLibUnix.signal_add
        except ImportError:
            self.add_signal = GLib.unix_signal_add
        self.loop = GLib.MainLoop()
        self.changed = changed
        self.graph = Graph()
        self.stream = JSONStream()
        self.process = None
        self.watcher = None
        self.sources = {}
        self.host_key = None
        self.host_dirty = True
        self.mode = None
        self.error = None
        self.closed = False

    def remove_source(self, name):
        ident = self.sources.pop(name, None)
        if ident:
            self.GLib.source_remove(ident)

    def later(self, name, delay, callback):
        if name in self.sources or self.closed:
            return
        def invoke():
            self.sources.pop(name, None)
            if not self.closed:
                callback()
            return False
        self.sources[name] = self.GLib.timeout_add(delay, invoke)

    def notify(self):
        self.later('reconcile', 40, self.changed)

    def retry(self):
        if self.host_key is not None and self.host_key[1] is False:
            return  # The host's session-active event will resume synchronization.
        self.later('retry', 1000, self.refresh_host)

    def refresh_host(self):
        self.host_dirty = True
        self.notify()

    def fallback_tick(self):
        self.refresh_host()
        self.later('fallback', 1000, self.fallback_tick)

    def read_host(self):
        self.remove_source('expiry')
        key, remaining = host_revision()
        mode = 'events' if key else 'compatibility polling'
        if mode != self.mode:
            print('Audio host updates:', mode, flush=True)
            self.mode = mode
        if key:
            self.remove_source('fallback')
            if key != self.host_key:
                self.refresh_host()
            self.later('expiry', int(remaining * 1000), self.read_host)
        else:
            self.later('fallback', 1000, self.fallback_tick)
        self.host_key = key

    def watch_status(self):
        if self.watcher:
            self.watcher.cancel()
        self.watcher = None
        try:
            self.watcher = self.Gio.File.new_for_path(str(STATUS.parent)).monitor_directory(
                self.Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.watcher.connect('changed', self.status_changed)
        except self.GLib.Error:
            self.later('watch', 1000, self.watch_status)
        self.read_host()

    def status_changed(self, monitor, file, other, event):
        paths = [p.get_path() for p in (file, other) if p is not None]
        if str(STATUS.parent) in paths:
            self.later('watch', 1000, self.watch_status)
        if str(STATUS) in paths or str(STATUS.parent) in paths:
            self.read_host()

    def receive(self, fd, condition):
        try:
            data = os.read(fd, 65536)
            if not data:
                raise EOFError('PipeWire monitor disconnected')
            for batch in self.stream.feed(data):
                if self.graph.update(batch):
                    self.notify()
            return True
        except BlockingIOError:
            return True
        except (OSError, EOFError, ValueError) as error:
            self.error = error
            self.sources.pop('pipewire', None)
            self.loop.quit()
            return False

    def snapshot(self):
        def query(ident):
            return json.loads(subprocess.check_output(['pw-dump', '-N', str(ident)], timeout=5))
        return self.graph.snapshot(query)

    def run(self, duration):
        self.process = subprocess.Popen(['pw-dump', '-m', '-N'], stdout=subprocess.PIPE)
        os.set_blocking(self.process.stdout.fileno(), False)
        flags = self.GLib.IO_IN | self.GLib.IO_HUP | self.GLib.IO_ERR
        self.sources['pipewire'] = self.GLib.io_add_watch(self.process.stdout.fileno(), self.GLib.PRIORITY_DEFAULT, flags, self.receive)
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.sources['signal' + str(sig)] = self.add_signal(self.GLib.PRIORITY_DEFAULT, sig, self.quit)
        if duration:
            self.later('duration', duration * 1000, self.loop.quit)
        self.watch_status()
        self.loop.run()
        if self.error:
            raise RuntimeError(str(self.error))

    def quit(self):
        self.loop.quit()
        return True

    def close(self):
        self.closed = True
        for name in list(self.sources):
            self.remove_source(name)
        if self.watcher:
            self.watcher.cancel()
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait()
            self.process.stdout.close()
