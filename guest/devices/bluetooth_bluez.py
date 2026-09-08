#!/usr/bin/env python3
"""BlueZ management API backed by macOS. No ACL/HID/audio payload emulation."""
from pathlib import Path
import json
import argparse
import fcntl
import os
import struct
import concurrent.futures
import re
import signal
from gi.repository import Gio, GLib
from bluetooth_management import request, device_operation

SOCKET = '/run/linuxhost-devices/control.sock'
ADAPTER = '/org/bluez/hci0'
ADAPTER_IFACE = 'org.bluez.Adapter1'
DEVICE_IFACE = 'org.bluez.Device1'
MANAGER = 'org.freedesktop.DBus.ObjectManager'
AGENTS = 'org.bluez.AgentManager1'
XML = '''<node>
<interface name="org.freedesktop.DBus.ObjectManager">
<method name="GetManagedObjects"><arg type="a{oa{sa{sv}}}" direction="out"/></method>
<signal name="InterfacesAdded"><arg type="o"/><arg type="a{sa{sv}}"/></signal>
<signal name="InterfacesRemoved"><arg type="o"/><arg type="as"/></signal>
</interface>
<interface name="org.bluez.Adapter1">
<method name="StartDiscovery"/><method name="StopDiscovery"/>
<method name="SetDiscoveryFilter"><arg type="a{sv}" direction="in"/></method>
<method name="GetDiscoveryFilters"><arg type="as" direction="out"/></method>
<method name="RemoveDevice"><arg type="o" direction="in"/></method>
<property name="Address" type="s" access="read"/>
<property name="AddressType" type="s" access="read"/>
<property name="Name" type="s" access="read"/>
<property name="Alias" type="s" access="read"/>
<property name="Class" type="u" access="read"/>
<property name="Powered" type="b" access="read"/>
<property name="Discoverable" type="b" access="read"/>
<property name="Pairable" type="b" access="read"/>
<property name="Discovering" type="b" access="read"/>
<property name="UUIDs" type="as" access="read"/>
</interface>
<interface name="org.bluez.Device1">
<method name="Connect"/><method name="Disconnect"/><method name="Pair"/>
<method name="CancelPairing"/>
<property name="Address" type="s" access="read"/>
<property name="AddressType" type="s" access="read"/>
<property name="Name" type="s" access="read"/>
<property name="Alias" type="s" access="read"/>
<property name="Class" type="u" access="read"/>
<property name="Icon" type="s" access="read"/>
<property name="Paired" type="b" access="read"/>
<property name="Bonded" type="b" access="read"/>
<property name="Connected" type="b" access="read"/>
<property name="Trusted" type="b" access="read"/>
<property name="Blocked" type="b" access="read"/>
<property name="ServicesResolved" type="b" access="read"/>
<property name="UUIDs" type="as" access="read"/>
<property name="Adapter" type="o" access="read"/>
<property name="LegacyPairing" type="b" access="read"/>
</interface>
<interface name="org.bluez.AgentManager1">
<method name="RegisterAgent"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
<method name="UnregisterAgent"><arg type="o" direction="in"/></method>
<method name="RequestDefaultAgent"><arg type="o" direction="in"/></method>
</interface>
</node>'''
INFO = Gio.DBusNodeInfo.new_for_xml(XML)


def variant(value):
    if isinstance(value, bool): return GLib.Variant('b', value)
    if isinstance(value, int): return GLib.Variant('u', value)
    if isinstance(value, list): return GLib.Variant('as', value)
    return GLib.Variant('s', value)


def device_props(record):
    address = record['address'].replace('-', ':').upper()
    if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', address):
        raise ValueError('Invalid host device address')
    cod = record['classOfDevice']
    if isinstance(cod, bool) or not isinstance(cod, int) or not 0 <= cod <= 0xffffff:
        raise ValueError('Invalid device class')
    uuids = record.get('uuids', [])
    if not isinstance(uuids, list) or len(uuids) > 32 or any(not isinstance(u, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', u) for u in uuids):
        raise ValueError('Invalid host service UUIDs')
    major = (cod >> 8) & 31
    icon = 'audio-headset' if major == 4 else 'input-mouse' if major == 5 else 'bluetooth'
    values = {'Address': address, 'AddressType': 'public', 'Name': record['name'],
              'Alias': record['name'], 'Class': cod, 'Icon': icon,
              'Paired': record['paired'], 'Bonded': record['paired'],
              'Connected': record['connected'], 'Trusted': False, 'Blocked': False,
              'ServicesResolved': False, 'UUIDs': uuids, 'LegacyPairing': False}
    # Host pairing is real; Linux service/profile resolution is deliberately not claimed.
    props = {key: variant(value) for key, value in values.items()}
    props['Adapter'] = GLib.Variant('o', ADAPTER)
    return ADAPTER + '/dev_' + address.replace(':', '_'), props


class Bridge:
    def __init__(self, bus, manage_radio=False):
        self.bus = bus
        self.manage_radio = manage_radio
        self.objects = {'/org/bluez': {AGENTS: {}}}
        self.agents = {}
        self.default_agent = None
        self.registrations = {}
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.pending = False
        self.refreshing = False
        self.closed = False
        self.discovery = set()
        self.register('/', MANAGER)
        self.register('/org/bluez', AGENTS)
        self.owner_signal = bus.signal_subscribe('org.freedesktop.DBus',
            'org.freedesktop.DBus', 'NameOwnerChanged', '/org/freedesktop/DBus',
            None, Gio.DBusSignalFlags.NONE, self.owner_changed)

    def owner_changed(self, connection, sender, path, iface, signal_name, args):
        name, old_owner, new_owner = args.unpack()
        if not new_owner:
            self.discovery.discard(name)
            self.agents.pop(name, None)
            if self.default_agent and self.default_agent[0] == name:
                self.default_agent = None


    def register(self, path, iface):
        key = (path, iface)
        self.registrations[key] = self.bus.register_object(path, INFO.lookup_interface(iface), self.method,
            lambda connection, sender, object_path, interface_name, prop: self.objects.get(object_path, {}).get(interface_name, {}).get(prop), None)

    def authorized(self, sender):
        result = self.bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetConnectionUnixUser', GLib.Variant('(s)', (sender,)),
            GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 3000, None)
        return result.unpack()[0] in (0, json.loads(Path('/etc/linuxhost.json').read_text())['userID'])

    def method(self, connection, sender, path, iface, method, args, invocation):
        if not self.authorized(sender):
            invocation.return_dbus_error('org.bluez.Error.NotAuthorized', 'Desktop user required'); return
        if iface == AGENTS:
            values = args.unpack()
            agent_path = values[0]
            if method == 'RegisterAgent':
                if values[1] not in ('', 'DisplayOnly', 'DisplayYesNo', 'KeyboardOnly', 'NoInputNoOutput', 'KeyboardDisplay'):
                    invocation.return_dbus_error('org.bluez.Error.InvalidArguments', 'Unknown agent capability'); return
                if sender in self.agents:
                    invocation.return_dbus_error('org.bluez.Error.AlreadyExists', 'Agent already registered'); return
                self.agents[sender] = agent_path
            elif self.agents.get(sender) != agent_path:
                invocation.return_dbus_error('org.bluez.Error.DoesNotExist', 'Agent not registered'); return
            elif method == 'UnregisterAgent':
                self.agents.pop(sender)
                if self.default_agent == (sender, agent_path): self.default_agent = None
            elif method == 'RequestDefaultAgent':
                self.default_agent = (sender, agent_path)
            invocation.return_value(None); return
        if iface == MANAGER:
            invocation.return_value(GLib.Variant('(a{oa{sa{sv}}})', (self.objects,))); return
        if method == 'GetDiscoveryFilters':
            invocation.return_value(GLib.Variant('(as)', (['Transport'],))); return
        if method == 'SetDiscoveryFilter':
            filters = args.unpack()[0]
            if set(filters) - {'Transport'} or filters.get('Transport', 'auto') not in ('auto', 'bredr'):
                invocation.return_dbus_error('org.bluez.Error.NotSupported', 'Classic discovery only'); return
            invocation.return_value(None); return
        if method == 'StopDiscovery':
            self.discovery.discard(sender)
            invocation.return_value(None); return
        if method == 'StartDiscovery':
            self.discovery.add(sender)
            invocation.return_value(None)
            self.refresh(); return
        actions = {'Connect': 'bluetooth-connect', 'Disconnect': 'bluetooth-disconnect', 'Pair': 'bluetooth-pair'}
        if iface == DEVICE_IFACE and method in actions:
            if self.pending:
                invocation.return_dbus_error('org.bluez.Error.InProgress', 'Host operation in progress'); return
            address = self.objects[path][iface]['Address'].unpack()
            agent = (sender, self.agents[sender]) if sender in self.agents else self.default_agent
            def agent_call(member, signature, values):
                if not agent:
                    raise RuntimeError('No GNOME pairing agent registered')
                return self.bus.call_sync(agent[0], agent[1], 'org.bluez.Agent1', member,
                    GLib.Variant(signature, values), None, Gio.DBusCallFlags.NONE, 30000, None)
            def confirm(code):
                try:
                    agent_call('RequestConfirmation', '(ou)', (path, code))
                    return True
                except GLib.Error:
                    return False
            def display(code):
                agent_call('DisplayPasskey', '(ouq)', (path, code, 0))
            self.submit(lambda: device_operation(address, actions[method], SOCKET,
                confirm=confirm, display=display), invocation)
            return
        invocation.return_dbus_error('org.bluez.Error.NotSupported', 'Operation is not implemented by the host bridge')

    def submit(self, function, invocation=None):
        if invocation: self.pending = True
        else: self.refreshing = True
        future = self.pool.submit(function)
        def finished():
            if invocation: self.pending = False
            else: self.refreshing = False
            if self.closed: return False
            try:
                result = future.result()
                if invocation: invocation.return_value(None)
                else: self.update(result)
            except Exception as error:
                if invocation: invocation.return_dbus_error('org.bluez.Error.Failed', str(error))
                else: self.update({'devices': [], 'powered': False, 'unavailable': True})
            return False
        future.add_done_callback(lambda _: GLib.idle_add(finished))

    def refresh(self):
        if not self.refreshing and not self.closed:
            scan = bool(self.discovery) and not self.pending
            def read():
                if scan:
                    request({'action': 'bluetooth-scan'}, SOCKET)
                return request({'action': 'bluetooth-devices'}, SOCKET)
            self.submit(read)
        return GLib.SOURCE_CONTINUE

    def update(self, state):
        if self.manage_radio and os.geteuid() == 0:
            try:
                with open('/dev/linuxhost-bt-radio', 'rb', buffering=0) as radio:
                    fcntl.ioctl(radio, 0x40044c20, struct.pack('I', 0 if state.get('unavailable') else 1 | (2 if state['powered'] else 0)))
            except OSError:
                pass  # Optional during private-bus API tests.
        # Synthetic management-adapter identity, not the host's Bluetooth address.
        adapter = {'Address': '02:4C:48:42:54:01', 'AddressType': 'public',
                   'Name': 'Mac Bluetooth', 'Alias': 'Mac Bluetooth', 'Class': 0,
                   'Powered': state['powered'], 'Discoverable': False,
                   'Pairable': state['powered'], 'Discovering': bool(self.discovery), 'UUIDs': []}
        current = {'/org/bluez': {AGENTS: {}}, ADAPTER: {ADAPTER_IFACE: {k: variant(v) for k, v in adapter.items()}}}
        for record in state['devices']:
            path, props = device_props(record)
            current[path] = {DEVICE_IFACE: props}
        for path in set(self.objects) - set(current):
            interfaces = list(self.objects.pop(path))
            self.bus.emit_signal(None, '/', MANAGER, 'InterfacesRemoved', GLib.Variant('(oas)', (path, interfaces)))
            for iface in interfaces:
                self.bus.unregister_object(self.registrations.pop((path, iface)))
        for path, interfaces in current.items():
            if path not in self.objects:
                self.objects[path] = interfaces
                for iface in interfaces: self.register(path, iface)
                self.bus.emit_signal(None, '/', MANAGER, 'InterfacesAdded', GLib.Variant('(oa{sa{sv}})', (path, interfaces)))
            else:
                for iface, props in interfaces.items():
                    changed = {k: v for k, v in props.items() if self.objects[path][iface].get(k) != v}
                    self.objects[path][iface] = props
                    if changed:
                        self.bus.emit_signal(None, path, 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                            GLib.Variant('(sa{sv}as)', (iface, changed, [])))

    def close(self):
        self.closed = True
        self.bus.signal_unsubscribe(self.owner_signal)
        for registration in self.registrations.values(): self.bus.unregister_object(registration)
        self.pool.shutdown(wait=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system', action='store_true')
    parser.add_argument('--duration', type=int, default=120)
    args = parser.parse_args()
    if not 0 <= args.duration <= 600: parser.error('duration must be 0 (service) or 1..600')
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM if args.system else Gio.BusType.SESSION, None)
    # Register ObjectManager before publishing the well-known name. Clients
    # inspect it immediately on NameOwnerChanged and may not retry an early error.
    bridge = Bridge(bus, manage_radio=args.system)
    try:
        bridge.update(request({'action': 'bluetooth-devices'}, SOCKET))
    except Exception:
        bridge.update({'devices': [], 'powered': False, 'unavailable': True})
    # Never replace an existing BlueZ owner; tests use a private session bus first.
    result = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'RequestName', GLib.Variant('(su)', ('org.bluez', 4)), GLib.VariantType.new('(u)'),
        Gio.DBusCallFlags.NONE, 3000, None)
    if result.unpack()[0] != 1:
        bridge.close()
        raise SystemExit('org.bluez already owned; refusing replacement')
    loop = GLib.MainLoop()
    bridge.refresh()
    timer = GLib.timeout_add_seconds(2, bridge.refresh)
    if args.duration:
        GLib.timeout_add_seconds(args.duration, lambda: (loop.quit(), False)[1])
    signal.signal(signal.SIGTERM, lambda *_: loop.quit())
    try: loop.run()
    finally:
        GLib.source_remove(timer)
        bridge.close()
