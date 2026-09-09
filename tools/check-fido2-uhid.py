#!/usr/bin/env python3
"""Root-only disposable UHID test. Supports discovery/ping, never authentication.

Uses a separate device identity with no desktop uaccess rule. Never opens any
physical HID device or changes an installed service. Requires python-fido2.
"""
import importlib.util
import argparse
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import tempfile
import threading
import time

from fido2 import cbor
from fido2.ctap2 import Ctap2
from fido2.hid import CtapHidDevice, get_descriptor, open_connection

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fido_fixture', ROOT / 'guest/auth/fido2.py')
fido = importlib.util.module_from_spec(spec); spec.loader.exec_module(fido)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-udev', action='store_true', help='Simulate the access rule on this fixture only, using an extra temporary rules directory')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run this isolated kernel-interface check with sudo')
    device = fido.Device(unique='org.ashacky.fido2.fixture')
    stop, wake = socket.socketpair()
    selector = selectors.DefaultSelector()
    selector.register(device.fd, selectors.EVENT_READ, 'device')
    selector.register(stop, selectors.EVENT_READ, 'stop')
    errors = []
    hid = None
    def request(request_id, _, data):
        info = {1: ['FIDO_2_0'], 3: bytes(16), 4: {'rk': False, 'up': True, 'uv': True}, 5: fido.MAX_MESSAGE}
        # No credential generation/signing path exists in this fixture.
        hid.complete(request_id, b'\0' + cbor.encode(info) if data == b'\x04' else b'\x27')
    hid = fido.HID(device.emit, request, lambda _: None)
    def serve():
        try:
            while True:
                for key, _ in selector.select(hid.deadline()):
                    if key.data == 'stop':
                        return
                    device.receive(hid)
                hid.timeout()
        except BaseException as e:
            errors.append(e)
    worker = threading.Thread(target=serve, daemon=True); worker.start()
    def expired(*_):
        raise TimeoutError('UHID fixture timed out')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(15)
    try:
        target = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for entry in Path('/sys/class/hidraw').glob('hidraw*'):
                if 'HID_UNIQ=org.ashacky.fido2.fixture\n' in (entry / 'device/uevent').read_text():
                    target = '/dev/' + entry.name
            if target:
                break
            time.sleep(0.02)
        assert target, 'Fixture was not created'
        if args.check_udev:
            # Copy only the identity matcher to the fixture's unique name. The
            # temporary rule is never installed or loaded by the system daemon.
            with tempfile.TemporaryDirectory(prefix='ashacky-fido-udev-') as directory:
                rule = (ROOT / 'guest/udev/70-ashacky-fido2.rules').read_text()
                rule = rule.replace('"org.ashacky.fido2"', '"org.ashacky.fido2.fixture"')
                (Path(directory) / '70-ashacky-fido2.rules').write_text(rule)
                subprocess.run(['udevadm', 'settle', '--timeout=3'], check=True, timeout=4)
                probe = subprocess.run(['udevadm', 'test', '--extra-rules-dir=' + directory, target],
                    text=True, capture_output=True, check=True, timeout=5)
                assert 'HID_UNIQ=org.ashacky.fido2.fixture' in probe.stdout
                assert any(line.strip().startswith('TAGS=') and ':uaccess:' in line for line in probe.stdout.splitlines()), probe.stdout
                print('Udev parent identity match and active-seat access tag passed on the disposable fixture')
        descriptor = get_descriptor(target)
        with CtapHidDevice(descriptor, open_connection(descriptor)) as client:
            ctap = Ctap2(client)
            assert ctap.info.versions == ['FIDO_2_0']
            payload = bytes(i % 251 for i in range(fido.MAX_MESSAGE))
            assert client.ping(payload) == payload
            print(f'Yubico client discovered the disposable UHID key, completed INIT/GetInfo and a {fido.MAX_MESSAGE}-byte fragmented PING')
    finally:
        signal.alarm(0)
        wake.send(b'x'); worker.join(timeout=5)
        selector.close(); stop.close(); wake.close(); device.close()
    assert not worker.is_alive()
    if errors:
        raise errors[0]
    print('Disposable device removed; no authentication, live device or service was changed')


if __name__ == '__main__':
    main()
