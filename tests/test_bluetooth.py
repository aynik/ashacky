import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'guest/devices'))
import bluetooth_events as events


def snapshot(revision='test:1', active=True, wake=0):
    return {'ok': True, 'statusTransport': 'virtio-serial',
            'bluetoothRevision': revision, 'active': active, 'wakeGeneration': wake}


def pump_until(predicate, timeout=3):
    from gi.repository import GLib
    context = GLib.MainContext.default()
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError('GLib event did not arrive before the deadline')


def pump_for(seconds):
    limit = time.monotonic() + seconds
    pump_until(lambda: time.monotonic() >= limit)


class BluetoothTests(unittest.TestCase):
    def test_revision_validation_and_freshness(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'status.json'
            self.assertIsNone(events.host_revision(path)[0])
            for value in ([], {'ok': True}, snapshot(''), snapshot(42), snapshot('x' * 129),
                          dict(snapshot(), statusTransport='rpc-poll')):
                path.write_text(json.dumps(value))
                self.assertIsNone(events.host_revision(path)[0])
            path.write_text(json.dumps(snapshot()))
            self.assertEqual(events.host_revision(path)[0], ('test:1', True, 0))
            os.utime(path, (1, 1))
            self.assertIsNone(events.host_revision(path)[0])

    @unittest.skipUnless(sys.platform == 'linux', 'GIO/Linux integration')
    def test_atomic_notifications_ignore_heartbeats_and_recover_after_directory_replacement(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name) / 'cache'; directory.mkdir()
            path = directory / 'status.json'
            def write(value):
                temp = directory / 'status.tmp'
                temp.write_text(json.dumps(value)); temp.replace(path)
            write(snapshot())
            seen, heartbeats = [], []
            watcher = events.BluetoothEvents(lambda: seen.append(watcher.key), path,
                                             heartbeat=lambda: heartbeats.append(watcher.key))
            try:
                watcher.watch_status()
                pump_until(lambda: len(seen) == 1)
                heartbeat_count = len(heartbeats)
                write(snapshot()); pump_for(0.1)
                self.assertEqual(len(seen), 1, 'Unchanged heartbeat caused a device query')
                self.assertGreater(len(heartbeats), heartbeat_count, 'Radio watchdog did not receive the heartbeat')
                write(snapshot('test:2')); pump_until(lambda: len(seen) == 2)
                write(snapshot('test:2', False)); pump_until(lambda: len(seen) == 3)
                self.assertFalse(watcher.active)
                watcher.retry(); self.assertNotIn('retry', watcher.sources)
                write(snapshot('test:2', True, 1)); pump_until(lambda: len(seen) == 4)
                # Atomic cache directory recreation happens during service recovery.
                path.unlink(); directory.rmdir(); directory.mkdir()
                write(snapshot('new-app:1'))
                pump_until(lambda: seen[-1] == ('new-app:1', True, 0))
                write(snapshot('new-app:2'))
                pump_until(lambda: seen[-1] == ('new-app:2', True, 0))
            finally:
                watcher.close()
            count = len(seen)
            write(snapshot('closed:1')); pump_for(0.1)
            self.assertEqual(len(seen), count)
            self.assertFalse(watcher.sources)

    @unittest.skipUnless(sys.platform == 'linux', 'GIO/Linux integration')
    def test_legacy_and_expired_stream_use_reconciliation_then_return_to_events(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'status.json'
            seen = []
            watcher = events.BluetoothEvents(lambda: seen.append(watcher.mode), path)
            try:
                watcher.watch_status()
                pump_until(lambda: seen)
                self.assertEqual(seen[-1], 'compatibility polling')
                pump_until(lambda: len(seen) >= 2)  # A real fallback tick.
                path.write_text(json.dumps(snapshot()))
                pump_until(lambda: seen[-1].startswith('events'))
                os.utime(path, (1, 1))
                watcher.read_host()  # Exercise the expiry timer's action without a 25s wait.
                pump_until(lambda: seen[-1] == 'compatibility polling')
                path.write_text(json.dumps(snapshot('new:1')))
                pump_until(lambda: watcher.key == ('new:1', True, 0))
            finally:
                watcher.close()

    @unittest.skipUnless(sys.platform == 'linux' and shutil.which('dbus-run-session'), 'Private Linux D-Bus')
    def test_private_bluez_bus_events_and_inflight_refresh(self):
        result = subprocess.run(['dbus-run-session', '--', sys.executable, str(Path(__file__)), '--private-bus'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def private_bus_checks():
    """Exercise the real ObjectManager/property signals on an isolated bus only."""
    import copy
    import threading
    from unittest.mock import patch
    from gi.repository import Gio, GLib
    import bluetooth_bluez as bluez
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    peer = Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    state = {'ok': True, 'powered': True, 'devices': [{'address': '02:00:00:00:00:01', 'name': 'Test input',
        'classOfDevice': 0x2580, 'paired': True, 'connected': False, 'uuids': []}]}
    entered, release = threading.Event(), threading.Event()
    holding = False
    requests = []
    def request(value, path):
        requests.append(value['action'])
        result = copy.deepcopy(state)
        if holding and not entered.is_set():
            entered.set()
            assert release.wait(2)
        return result
    bridge = bluez.Bridge(bus)
    bridge.update(state)
    result = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'RequestName', GLib.Variant('(su)', ('org.bluez', 4)), GLib.VariantType.new('(u)'),
        Gio.DBusCallFlags.NONE, 3000, None)
    assert result.unpack()[0] == 1
    changed = []
    subscription = peer.signal_subscribe(None, 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
        None, None, Gio.DBusSignalFlags.NONE, lambda *args: changed.append(args[-1].unpack()))
    def call(path, iface, method):
        reply = []
        def completed(connection, result):
            reply.append(connection.call_finish(result))
        peer.call('org.bluez', path, iface, method, None, None, Gio.DBusCallFlags.NONE, 2000, None, completed)
        pump_until(lambda: reply)
        return reply[0].unpack()
    try:
        with patch.object(bluez, 'request', request), patch.object(bridge, 'authorized', return_value=True):
            objects = call('/', bluez.MANAGER, 'GetManagedObjects')[0]
            device = bluez.ADAPTER + '/dev_02_00_00_00_00_01'
            assert objects[device][bluez.DEVICE_IFACE]['Connected'] is False
            # An event during an in-flight snapshot must force one more read.
            holding = True
            bridge.refresh(); pump_until(entered.is_set)
            state['devices'][0]['connected'] = True
            bridge.refresh(); release.set()
            pump_until(lambda: any(c[1].get('Connected') is True for c in changed))
            assert requests == ['bluetooth-devices', 'bluetooth-devices']
            assert call('/', bluez.MANAGER, 'GetManagedObjects')[0][device][bluez.DEVICE_IFACE]['Connected'] is True
            count = len(requests); pump_for(0.1); assert len(requests) == count
            call(bluez.ADAPTER, bluez.ADAPTER_IFACE, 'StartDiscovery')
            pump_until(lambda: not bridge.refreshing and 'discovery' in bridge.events.sources)
            assert requests.count('bluetooth-scan') == 1
            call(bluez.ADAPTER, bluez.ADAPTER_IFACE, 'StopDiscovery')
            assert 'discovery' not in bridge.events.sources
            assert call('/', bluez.MANAGER, 'GetManagedObjects')[0][bluez.ADAPTER][bluez.ADAPTER_IFACE]['Discovering'] is False
            # A host lock event suppresses device queries until host activation.
            bridge.events.key = ('test:1', False, 0)
            count = len(requests); bridge.refresh(); pump_for(0.1)
            assert len(requests) == count
            # Heartbeats may maintain only state validated for this revision.
            bridge.events.key = ('test:1', True, 0)
            bridge.update(state)
            with patch.object(bridge, 'write_radio') as write:
                bridge.heartbeat(); write.assert_called_once()
                bridge.events.key = ('test:2', True, 0)
                bridge.heartbeat(); write.assert_called_once()
                bridge.events.key = None
                bridge.heartbeat(); write.assert_called_once()
    finally:
        release.set()
        bridge.close()
        peer.signal_unsubscribe(subscription)
        peer.close_sync(None)


if __name__ == '__main__':
    if '--private-bus' in sys.argv:
        private_bus_checks()
    else:
        unittest.main()
