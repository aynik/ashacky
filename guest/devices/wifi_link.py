"""Host-selected BSS validation and generation-bound cfg80211 link reports."""
import base64
import ctypes
import errno
import fcntl
import os
import struct
import time
from wifi_events import STATUS, host_revision
from wifi_scan import scan

LINK = struct.Struct('<IBBH6s32s')
REPORT = struct.Struct('<IBBHIiHH6s32s')
GET_LINK = 0x80000000 | LINK.size << 16 | 0x4C08
REPORT_LINK = 0x40000000 | REPORT.size << 16 | 0x4C09
CONNECTED, PRIVATE, PINNED, BUSY = 1, 2, 4, 8
CCMP, PSK = b'\x00\x0f\xac\x04', b'\x00\x0f\xac\x02'


def compatible(record, private):
    ies, offset, rsns, wpa = record.information_elements, 0, [], False
    while offset < len(ies):
        if offset + 2 > len(ies) or offset + 2 + ies[offset + 1] > len(ies):
            return False
        tag, size = ies[offset:offset + 2]
        value = ies[offset + 2:offset + 2 + size]
        if tag == 48: rsns.append(value)
        if tag == 221 and value.startswith(b'\x00\x50\xf2\x01'): wpa = True
        offset += 2 + size
    if not private: return record.open and not rsns and not wpa
    if record.open or len(rsns) != 1: return False
    value = rsns[0]
    if len(value) < 18 or value[:2] != b'\x01\x00' or value[2:6] != CCMP: return False
    offset = 6
    for required in (CCMP, PSK):
        if offset + 2 > len(value): return False
        count = int.from_bytes(value[offset:offset + 2], 'little'); offset += 2
        if not count or count > (len(value) - offset) // 4: return False
        suites = [value[i:i + 4] for i in range(offset, offset + count * 4, 4)]
        if required not in suites: return False
        offset += count * 4
    return offset == len(value) or (len(value) - offset >= 2 and not int.from_bytes(value[offset:offset + 2], 'little') & 64)


def identity(state):
    if state.get('locationAuthorized') is not True:
        raise RuntimeError('Host Wi-Fi identity unavailable')
    if not state.get('networkIdentityAvailable'):
        return None
    ssid = base64.b64decode(state.get('ssidBase64', ''), validate=True)
    address = bytes.fromhex(state.get('bssid', '').replace(':', ''))
    if not 1 <= len(ssid) <= 32 or len(address) != 6 or not any(address) or address[0] & 1:
        raise ValueError('Invalid host association identity')
    return ssid, address


def actual_bss(records, target, address, private):
    matches = [r for r in records if r.ssid == target and r.bssid == address and compatible(r, private)]
    if len(matches) != 1:
        raise RuntimeError('Associated AP lacks a matching supported security record')
    return matches[0]


def report(device, sequence, state, record=None):
    if record is None:
        payload = REPORT.pack(sequence, state, 0, 0, 0, 0, 0, 0, bytes(6), bytes(32))
    else:
        payload = REPORT.pack(sequence, state, len(record.ssid), len(record.information_elements),
            record.frequency_mhz, record.signal_mbm, 1 | (0 if record.open else 16), 0,
            record.bssid, record.ssid) + record.information_elements
    fcntl.ioctl(device, REPORT_LINK, payload)


class LinkFollower:
    def __init__(self, device):
        self.device = device
        self.missing = None

    def snapshot(self):
        data = bytearray(LINK.size)
        fcntl.ioctl(self.device, GET_LINK, data)
        sequence, flags, length, reserved, address, ssid = LINK.unpack(data)
        if flags & ~15 or reserved or length > 32 or (flags & CONNECTED and
                (not length or len(address) != 6 or not any(address) or address[0] & 1)):
            raise ValueError('Invalid kernel link snapshot')
        return sequence, flags, address, ssid[:length]

    def update(self, link, state, rpc):
        sequence, flags, address, target = link
        if not flags & CONNECTED or flags & BUSY:
            self.missing = None; return
        current = identity(state)
        if current is None:
            # Confirm a transient identity gap before reporting actual loss.
            if self.missing is None or self.missing[0] != sequence:
                self.missing = sequence, time.monotonic() + 1
                return
            if time.monotonic() < self.missing[1]: return
        self.missing = None
        if current is None or current[0] != target or (flags & PINNED and current[1] != address):
            report(self.device, sequence, 0)
            print('Wi-Fi host link disconnected', flush=True)
            return
        if current[1] == address: return
        records = scan(rpc)
        selected = actual_bss(records, target, current[1], bool(flags & PRIVATE))
        # A scan can take seconds. Do not report a BSS the host has since left.
        if identity(rpc({'action': 'wifi-status'})) != current: return
        report(self.device, sequence, 1, selected)
        print('Wi-Fi host roam reported', flush=True)


class StatusWatch:
    """Wake the existing kernel poll on status replacement, without a thread.

    An unchanged heartbeat is not a request for another host snapshot. The
    existing signal deadline also bounds recovery if the directory is absent.
    """
    EVENT = struct.Struct('iIII')
    def __init__(self, path=STATUS):
        self.path, self.fd, self.key = path, None, None
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.inotify_init1.argtypes = [ctypes.c_int]
        self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]

    def open(self):
        if self.fd is not None: return
        fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0: raise OSError(ctypes.get_errno(), 'Wi-Fi status inotify initialization failed')
        if self.libc.inotify_add_watch(fd, os.fsencode(self.path.parent), 0x00000F88) < 0:
            error = ctypes.get_errno(); os.close(fd)
            if error == errno.ENOENT: return
            raise OSError(error, 'Wi-Fi status directory watch failed')
        self.fd = fd

    def changed(self):
        invalid = False
        if self.fd is not None:
            while True:
                try: data = os.read(self.fd, 65536)
                except BlockingIOError: break
                if not data: raise OSError('Wi-Fi status watch closed')
                offset = 0
                while offset < len(data):
                    if len(data) - offset < self.EVENT.size: raise ValueError('Incomplete status event')
                    _, mask, _, length = self.EVENT.unpack_from(data, offset)
                    if offset + self.EVENT.size + length > len(data): raise ValueError('Incomplete status name')
                    if mask & 0x0000CC00: invalid = True  # Overflow / watch moved or removed.
                    offset += self.EVENT.size + length
            if invalid: self.close()
        key, _ = host_revision(self.path)
        changed = key != self.key
        self.key = key
        return invalid or changed

    def close(self):
        if self.fd is not None: os.close(self.fd); self.fd = None
