"""Host Bluetooth invalidations; no device identities in the telemetry cache."""
import json
from pathlib import Path
import time

STATUS = Path('/run/linuxhost/status.json')


def host_revision(path=STATUS):
    try:
        age = max(0, time.time() - path.stat().st_mtime)
        value = json.loads(path.read_text())
        revision = value.get('bluetoothRevision')
        if (value.get('ok') is True and value.get('statusTransport') == 'virtio-serial'
                and isinstance(revision, str) and 0 < len(revision) <= 128 and age < 25):
            return (revision, value.get('active'), value.get('wakeGeneration')), max(1, 25 - age)
    except (OSError, ValueError, AttributeError):
        pass
    return None, 0


class BluetoothEvents:
    def __init__(self, changed, path=STATUS, heartbeat=None):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.changed, self.path = changed, path
        self.heartbeat = heartbeat
        self.sources = {}
        self.watcher = None
        self.key = None
        self.mode = None
        self.closed = False

    @property
    def active(self):
        return self.key is None or self.key[1] is not False

    def remove_source(self, name):
        ident = self.sources.pop(name, None)
        if ident:
            self.GLib.source_remove(ident)

    def later(self, name, milliseconds, callback):
        if self.closed or name in self.sources:
            return
        def invoke():
            self.sources.pop(name, None)
            if not self.closed:
                callback()
            return False
        self.sources[name] = self.GLib.timeout_add(milliseconds, invoke)

    def notify(self):
        self.later('refresh', 40, self.changed)

    def retry(self):
        if self.active:
            self.later('retry', 2000, self.notify)

    def reconcile(self):
        if self.active:
            self.notify()
        # External pairing removal has no public global notification. Retain a
        # slow snapshot for this, missed callbacks and notification rearming.
        self.later('reconcile', 60000 if self.key else 2000, self.reconcile)

    def read_host(self):
        self.remove_source('expiry')
        key, remaining = host_revision(self.path)
        mode = 'events (60-second reconciliation)' if key else 'compatibility polling'
        if mode != self.mode:
            print('Bluetooth host updates:', mode, flush=True)
            self.mode = mode
            self.remove_source('reconcile')
            self.later('reconcile', 60000 if key else 2000, self.reconcile)
            self.notify()
        if key != self.key:
            self.key = key
            self.notify()
        if key:
            self.later('expiry', int(remaining * 1000), self.read_host)
            if self.heartbeat:
                self.heartbeat()

    def watch_status(self):
        if self.watcher:
            self.watcher.cancel()
        self.watcher = None
        try:
            self.watcher = self.Gio.File.new_for_path(str(self.path.parent)).monitor_directory(
                self.Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.watcher.connect('changed', self.status_changed)
        except self.GLib.Error:
            self.later('watch', 1000, self.watch_status)
        self.read_host()

    def status_changed(self, monitor, file, other, event):
        paths = [p.get_path() for p in (file, other) if p is not None]
        if str(self.path.parent) in paths:
            self.later('watch', 1000, self.watch_status)
        if str(self.path) in paths or str(self.path.parent) in paths:
            self.read_host()

    def close(self):
        self.closed = True
        for name in list(self.sources):
            self.remove_source(name)
        if self.watcher:
            self.watcher.cancel()
