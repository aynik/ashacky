#!/usr/bin/env python3
"""Synchronize the Linux Wi-Fi rfkill switch with the host CoreWLAN radio."""
import glob
from pathlib import Path
import struct
import time
from bluetooth_management import request

SOCKET = '/run/linuxhost-wifi-test.sock'


def switch():
    entries = glob.glob('/sys/class/net/lhwifi0/phy80211/rfkill*')
    if len(entries) != 1:
        raise RuntimeError('Wi-Fi radio interface unavailable')
    path = Path(entries[0])
    return int(path.name[6:]), int((path / 'soft').read_text())


def set_switch(index, blocked):
    # Linux UAPI rfkill_event: idx, type WLAN, operation CHANGE, soft, hard.
    with open('/dev/rfkill', 'wb', buffering=0) as device:
        if device.write(struct.pack('<IBBBB', index, 1, 2, int(blocked), 0)) != 8:
            raise RuntimeError('Incomplete radio state write')


def powered():
    state = request({'action': 'wifi-status'}, SOCKET)
    if type(state.get('powered')) is not bool:
        raise ValueError('Host radio state missing')
    return state['powered']


def run():
    previous = None
    previous_index = None
    failed = False
    while True:
        try:
            index, blocked = switch()
            if index != previous_index:
                previous = None
            if previous is not None and blocked != previous:
                request({'action': 'wifi-power', 'enabled': not bool(blocked)}, SOCKET)
                # Readback, rather than RPC acknowledgement, confirms radio state.
            actual = powered()
            new_index, latest = switch()
            if new_index != index or latest != blocked:
                # Another Linux toggle happened during host I/O; handle it next.
                previous = blocked
                previous_index = index
                continue
            desired = int(not actual)
            if blocked != desired:
                set_switch(index, desired)
            previous = desired
            previous_index = index
            if failed:
                print('Wi-Fi radio synchronization recovered', flush=True)
            failed = False
        except (OSError, RuntimeError, ValueError) as error:
            if not failed:
                print('Wi-Fi radio synchronization unavailable: ' + type(error).__name__, flush=True)
            # Do not retry an uncertain power mutation blindly. Resynchronize
            # from actual host state before accepting another user transition.
            previous = None
            failed = True
        time.sleep(1)


if __name__ == '__main__':
    run()
