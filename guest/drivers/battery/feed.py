#!/usr/bin/python3
"""Notify the virtual battery as soon as the host status cache is replaced."""
import ctypes
import json
import os
from pathlib import Path
import select
import struct
import time

SOURCE = Path('/run/linuxhost/status.json')
STATE = Path('/sys/devices/platform/linuxhost-battery/state')


def battery_state(data):
    if data.get('ok') is not True:
        raise ValueError('Host unavailable')
    battery = data['battery']
    current, maximum = battery['Current Capacity'], battery['Max Capacity']
    charging, power = battery['Is Charging'], battery['Power Source State']
    if (type(current) is not int or type(maximum) is not int or maximum <= 0
            or not 0 <= current <= maximum or type(charging) is not bool
            or power not in ('AC Power', 'Battery Power')):
        raise ValueError('Invalid telemetry')
    return f'1 {int(power == "AC Power")} {round(current * 100 / maximum)} {int(charging)}\n'


def publish(source=SOURCE, state=STATE):
    try:
        if time.time() - source.stat().st_mtime > 20:
            return False
        value = battery_state(json.loads(source.read_text()))
        state.write_text(value)
        return True
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        # The driver's watchdog marks missing/stale telemetry unavailable.
        return False


class StatusWatch:
    """Watch the directory: the agent atomically renames each new snapshot."""
    def __init__(self, source):
        self.name = os.fsencode(source.name)
        libc = ctypes.CDLL(None, use_errno=True)
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), 'inotify_init1')
        # CLOSE_WRITE | MOVED_TO | CREATE | DELETE_SELF | MOVE_SELF
        if libc.inotify_add_watch(self.fd, os.fsencode(source.parent), 0x00000D88) < 0:
            error = ctypes.get_errno()
            self.close()
            raise OSError(error, 'inotify_add_watch')

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def wait(self, timeout=15):
        if not select.select([self.fd], [], [], timeout)[0]:
            return False
        data = os.read(self.fd, 65536)
        offset, changed = 0, False
        while offset + 16 <= len(data):
            _, mask, _, size = struct.unpack_from('iIII', data, offset)
            name = data[offset + 16:offset + 16 + size].split(b'\0', 1)[0]
            offset += 16 + size
            # Directory replacement/removal or watch removal requires re-arming.
            if mask & 0x00008C00:
                raise OSError('Status directory replaced')
            if name == self.name or mask & 0x00004000:  # queue overflow: resnapshot
                changed = True
        return changed


def main():
    while True:
        watch = None
        try:
            watch = StatusWatch(SOURCE)
            publish()  # Install watch first so an update cannot race this read.
            while True:
                if watch.wait():
                    publish()
        except OSError:
            time.sleep(1)  # Retry only while the agent/runtime directory is absent.
        finally:
            if watch is not None:
                watch.close()


if __name__ == '__main__':
    main()
