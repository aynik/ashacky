"""Wi-Fi invalidations from the existing credential-free host status channel."""
import json
from pathlib import Path
import time

STATUS = Path('/run/linuxhost/status.json')


def host_revision(path=STATUS):
    try:
        age = max(0, time.time() - path.stat().st_mtime)
        value = json.loads(path.read_text())
        revision = value.get('wifiRevision')
        if (value.get('ok') is True and value.get('statusTransport') == 'virtio-serial'
                and isinstance(revision, str) and 0 < len(revision) <= 128 and age < 25):
            return (revision, value.get('active'), value.get('wakeGeneration')), max(1, 25 - age)
    except (OSError, ValueError, AttributeError):
        pass
    return None, 0


class WiFiEvents:
    def __init__(self, changed, path=STATUS):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.changed, self.path = changed, path
        self.key = None
        self.mode = None
        self.sources = {}
        self.watcher = None
        self.closed = False

    @property
    def active(self):
        return self.key is None or self.key[1] is not False

    def remove(self, name):
        source = self.sources.pop(name, None)
        if source:
            self.GLib.source_remove(source)

    def later(self, name, milliseconds, callback):
        if self.closed or name in self.sources:
            return
        def invoke():
            self.sources.pop(name, None)
            if not self.closed:
                callback()
            return False
        self.sources[name] = self.GLib.timeout_add(milliseconds, invoke)

    def fallback(self):
        self.changed()
        self.later('fallback', 1000, self.fallback)

    def read_host(self):
        self.remove('expiry')
        key, remaining = host_revision(self.path)
        mode = 'events' if key else 'compatibility polling'
        transition = key != self.key or mode != self.mode
        self.key = key
        if mode != self.mode:
            self.mode = mode
            print('Wi-Fi radio host updates:', mode, flush=True)
        if key:
            self.remove('fallback')
            self.later('expiry', int(remaining * 1000), self.read_host)
        else:
            self.later('fallback', 1000, self.fallback)
        if transition:
            self.changed()

    def watch(self):
        if self.watcher:
            self.watcher.cancel()
        self.watcher = None
        try:
            self.watcher = self.Gio.File.new_for_path(str(self.path.parent)).monitor_directory(
                self.Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.watcher.connect('changed', self.status_changed)
        except self.GLib.Error:
            self.later('watch', 1000, self.watch)
        self.read_host()

    def status_changed(self, monitor, file, other, event):
        paths = [item.get_path() for item in (file, other) if item is not None]
        if str(self.path.parent) in paths:
            self.later('watch', 1000, self.watch)
        if str(self.path) in paths or str(self.path.parent) in paths:
            self.read_host()

    def close(self):
        self.closed = True
        for name in list(self.sources):
            self.remove(name)
        if self.watcher:
            self.watcher.cancel()
