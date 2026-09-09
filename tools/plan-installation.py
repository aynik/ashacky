#!/usr/bin/env python3
"""Generate private host/guest configuration for review; never apply it."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shlex
import uuid

ROOT = Path(__file__).resolve().parents[1]


def validate(c):
    for name in ('hostUID', 'groupID', 'guestUID', 'memoryMiB', 'cpus'):
        if type(c[name]) is not int or c[name] <= 0:
            raise ValueError(name + ' must be a positive integer')
    for name in ('hostUser', 'group', 'guestUser'):
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', c[name]):
            raise ValueError('Invalid account/group name: ' + name)
    for name in ('guestManagementInterface', 'guestWifiInterface'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', c[name]):
            raise ValueError('Invalid guest interface: ' + name)
    if c['guestManagementInterface'] == c['guestWifiInterface']:
        raise ValueError('The two transport interfaces must be distinct')
    for name in ('checkout', 'privateDirectory', 'hostPython', 'hostHome'):
        value = c[name]
        if (not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts
                or re.search(r'[\x00-\x1f,]', value)):
            raise ValueError('Expected an absolute path without control characters or QEMU commas: ' + name)
    subnet = ipaddress.IPv4Network(c['managementSubnet'])
    host = ipaddress.IPv4Address(c['hostAddress'])
    guest = ipaddress.IPv4Address(c['fallbackAddress'])
    private = any(subnet.subnet_of(ipaddress.IPv4Network(block)) for block in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    if (subnet.prefixlen != 24 or not private or host not in subnet or guest not in subnet
            or host != subnet.network_address + 1 or guest != subnet.network_address + 2):
        raise ValueError('Choose a private /24 with host .1 and initial guest fallback .2')
    if len((c['privateDirectory'] + '/runtime/host.sock.supervisor').encode()) >= 104:
        raise ValueError('Private directory is too long for macOS Unix sockets')


def generate(c, destination):
    validate(c)
    # Exclusive creation prevents overwriting a reviewed identity or its token.
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = []

    def write(relative, data, target, mode=0o644, owner='root:root'):
        path = destination / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not isinstance(data, bytes):
            data = data.encode()
        path.write_bytes(data)
        path.chmod(mode)
        manifest.append({'file': relative, 'target': target, 'mode': oct(mode), 'owner': owner})

    def js(relative, value, target, owner='root:root'):
        write(relative, json.dumps(value, indent=2) + '\n', target, 0o600, owner)

    uid = c['hostUID']
    base = Path(c['privateDirectory'])
    checkout = Path(c['checkout'])
    app = checkout / 'build/host/Ashacky.app/Contents'
    root = Path('/Library/PrivilegedHelperTools') / ('Ashacky-' + str(uid))
    runtime = Path('/var/run') / ('ashacky-' + str(uid))
    token, identity = secrets.token_hex(32), str(uuid.uuid4())
    macs = ['02:' + ':'.join(f'{byte:02x}' for byte in secrets.token_bytes(5)) for _ in range(2)]
    while macs[0] == macs[1]:
        macs[1] = '02:' + ':'.join(f'{byte:02x}' for byte in secrets.token_bytes(5))
    owner = c['hostUser'] + ':' + c['group']
    ssh = str(checkout / 'host/transport/probe-ssh')
    env = {'LINUXHOST_SESSION_CONFIG': str(base / 'transport.json'),
           'LINUXHOST_CONTROL_CONFIG': str(base / 'control.json'),
           'ASHACKY_GUEST_SSH': ssh, 'ASHACKY_PYTHON': c['hostPython']}
    control = {'socket': str(base / 'runtime/host.sock'), 'token': token, 'guestSSH': ssh,
               'powerEnabled': False, 'powerSocket': str(runtime / 'power/power.sock')}
    vm = {'userID': uid, 'uuid': identity, 'diskSerial': 'ashacky-' + secrets.token_hex(8),
          'name': 'Ashacky', 'cpus': c['cpus'], 'memory': c['memoryMiB'],
          'appContents': str(app), 'runtime': str(base / 'runtime'), 'control': str(base / 'control.json'),
          'disk': str(base / 'guest.qcow2'), 'vars': str(base / 'vars.qcow2'),
          'shared': str(base / 'shared'), 'macs': macs,
          'networks': [str(runtime / 'network' / (name + '.sock')) for name in ('shared', 'wifi')],
          'usbSocket': str(runtime / 'usb/redirect.sock'), 'videoBindAddress': c['hostAddress']}
    js('host/private/vm.json', vm, str(base / 'vm.json'), owner)
    js('host/private/control.json', control, str(base / 'control.json'), owner)
    js('host/private/transport.json', {'uuid': identity, 'privateDirectory': str(base),
        'managementSubnet': c['managementSubnet'], 'fallbackAddress': c['fallbackAddress']}, str(base / 'transport.json'), owner)
    policy = str(Path('/Library/Application Support/Ashacky') / str(uid) / 'power-policy.json')
    js('host/root/power-policy.json', {'userID': uid, 'enabled': False, 'allowOtherSessions': False}, policy, 'root:wheel')
    js('helper-build.json', {'userID': uid, 'groupID': c['groupID'], 'usbRuntime': str(runtime / 'usb'),
        'powerRuntime': str(runtime / 'power'), 'powerPolicy': policy,
        'powerClients': [str(app / 'MacOS/Ashacky')]}, 'build input only', owner)

    def job(name, arguments, system=False, keep=True, extra=None):
        label = f'local.ashacky.{uid}.{name}'
        value = {'Label': label, 'ProgramArguments': list(map(str, arguments)), 'RunAtLoad': True,
                 'ThrottleInterval': 10, 'Umask': 0o077,
                 'AssociatedBundleIdentifiers': ['local.ashacky.host']}
        if keep:
            value['KeepAlive'] = True
        if not system:
            value.update(EnvironmentVariables=env, LimitLoadToSessionType='Aqua',
                StandardOutPath=str(base / 'logs' / (name + '.log')),
                StandardErrorPath=str(base / 'logs' / (name + '.log')))
        if system:
            log = f'/var/log/ashacky-{uid}-{name}.log'
            value.update(StandardOutPath=log, StandardErrorPath=log)
        if extra:
            value.update(extra)
        category = 'LaunchDaemons' if system else 'LaunchAgents'
        target = str((Path('/Library') if system else Path(c['hostHome']) / 'Library') / category / (label + '.plist'))
        write('host/' + category + '/' + label + '.plist', plistlib.dumps(value), target,
              owner='root:wheel' if system else owner)

    job('session', [c['hostPython'], checkout / 'host/session/session.py', '--config', base / 'vm.json'],
        keep=False, extra={'ProcessType': 'Interactive'})
    for name in ('shared', 'wifi'):
        job('network-' + name, [root / 'service.sh', 'network-' + name], system=True)
    job('usb', [root / 'service.sh', 'usb'], system=True)
    job('power', [root / 'service.sh', 'power'], system=True)
    q = lambda value: shlex.quote(str(value))
    # Every root job initializes the shared parent, regardless of startup order.
    # install -d creates intermediate directories using umask, so creating only
    # runtime/network under a 077 launchd umask strands the user behind mode 0700.
    service = '#!/bin/sh\nset -eu\n'
    service += 'test ! -L ' + q(runtime) + '\n'
    service += '/usr/bin/install -d -o root -g ' + q(c['group']) + ' -m 750 ' + q(runtime) + '\n'
    service += 'case "${1-}" in\n'
    for name in ('shared', 'wifi'):
        service += ' network-' + name + ') exec ' + q(root / 'network.sh') + ' ' + name + ' ;;\n'
    service += ' usb) exec ' + q(root / 'linuxhost-usb-host') + ' ;;\n'
    service += ' power) exec ' + q(root / 'LinuxHostPower') + ' ;;\n *) exit 2 ;;\nesac\n'
    write('host/root/service.sh', service, str(root / 'service.sh'), 0o755, 'root:wheel')
    network = '#!/bin/sh\nset -eu\n/usr/bin/install -d -o root -g ' + q(c['group']) + ' -m 750 ' + q(runtime / 'network') + '\n'
    network += 'case "$1" in\n shared) set -- --vmnet-mode=shared --vmnet-gateway=' + q(c['hostAddress']) + ' --vmnet-mask=255.255.255.0 ' + q(runtime / 'network/shared.sock') + ' ;;\n'
    network += ' wifi) interface=$(' + q(root / 'wifi-interface') + '); set -- --vmnet-mode=bridged "--vmnet-interface=$interface" ' + q(runtime / 'network/wifi.sock') + ' ;;\n *) exit 2 ;;\nesac\n'
    network += 'exec ' + q(root / 'socket_vmnet') + ' --socket-group=' + q(c['group']) + ' "$@"\n'
    write('host/root/network.sh', network, str(root / 'network.sh'), 0o755, 'root:wheel')
    js('guest/etc/linuxhost.json', {'userID': c['guestUID'], 'token': token, 'hostSocket': '/run/linuxhost-host.sock'}, '/etc/linuxhost.json')
    js('guest/etc/ashacky/devices.json', {'vmUUID': identity, 'wifiInterface': c['guestWifiInterface'],
        'wifiMAC': macs[1], 'cameraVideoNumber': 10}, '/etc/ashacky/devices.json')
    write('guest/etc/ashacky/video.env', 'LH_VIDEO_H264_HOST=' + c['hostAddress'] + ':5557\nLH_VIDEO_VP9_HOST=' + c['hostAddress'] + '\n', '/etc/ashacky/video.env')
    path = 'etc/NetworkManager/conf.d/90-ashacky-transport.conf'
    # The virtual Wi-Fi device inherits the lower NIC's MAC. A MAC-based
    # exclusion would also disable NetworkManager on the virtual radio.
    write('guest/' + path, '[keyfile]\nunmanaged-devices=' + ';'.join('interface-name:' + c[name]
        for name in ('guestManagementInterface', 'guestWifiInterface')) + '\n', '/' + path)
    path = 'etc/systemd/network/20-ashacky-management.network'
    write('guest/' + path, '[Match]\nMACAddress=' + macs[0] + '\n\n[Network]\nDHCP=ipv4\nIPv6AcceptRA=no\n\n[DHCPv4]\nRouteMetric=100\n', '/' + path)
    js('identity.json', {'uuid': identity, 'macs': macs, 'guestUser': c['guestUser'],
        'guestUID': c['guestUID'], 'rootDirectory': str(root), 'privateDirectory': str(base)}, 'review only', owner)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (destination / 'manifest.json').chmod(0o600)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.name):
        parser.error('--name must contain only letters, numbers, underscores or hyphens')
    os.umask(0o077)
    output = generate(json.loads(args.config.read_text()), ROOT / 'build/plans' / args.name)
    print('Private review files:', output)
    print('Nothing installed, downloaded, started, or changed outside build/plans.')


if __name__ == '__main__':
    main()
