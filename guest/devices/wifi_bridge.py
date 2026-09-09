"""Bridge real host scans into the experimental LinuxHost cfg80211 module.

Default transport is the production guest agent. --socket permits an isolated
SSH-forwarded workbench socket for development. Pending WPA2 keys are relayed
in memory only; never save or log the request payload.
"""
import argparse
import base64
import contextlib
import errno
import fcntl
import json
import math
import os
import select
import socket
import struct
import time
from wifi_scan import parse_bss, scan
from wifi_link import LinkFollower, StatusWatch, actual_bss, compatible, identity, report

GET_SCAN = 0x80044C01
FINISH_SCAN = 0x40044C02
ABORT_SCAN = 0x40044C03
CONNECTION = struct.Struct('<IBBBB6s32s32s')
GET_CONNECTION = 0x804E4C04
CONNECTION_RESULT = 0x40104C05
SIGNAL = 0x40084C06
GET_CAPABILITIES = 0x80044C07
CAP_REQUEST_POLL = 1
CAP_HOST_LINK = 2


class RequestWait:
    """Wait for unread kernel requests; old modules retain the bounded idle poll."""
    def __init__(self, device):
        capabilities = bytearray(4)
        try:
            fcntl.ioctl(device, GET_CAPABILITIES, capabilities)
        except OSError as error:
            if error.errno != errno.ENOTTY:
                raise
        self.poller = None
        self.capabilities = struct.unpack('=I', capabilities)[0]
        self.watched = None
        if self.capabilities & CAP_REQUEST_POLL:
            self.poller = select.poll()
            self.poller.register(device, select.POLLIN)
        print('Wi-Fi request delivery: ' + ('kernel notifications' if self.poller else 'compatibility polling'), flush=True)

    def watch(self, watcher):
        if self.poller is None or self.watched == watcher.fd: return
        if self.watched is not None: self.poller.unregister(self.watched)
        self.watched = watcher.fd
        if self.watched is not None: self.poller.register(self.watched, select.POLLIN)

    def wait(self, timeout=None):
        if self.poller is None:
            time.sleep(0.1 if timeout is None else min(0.1, max(0, timeout)))
            return False
        milliseconds = None if timeout is None else math.ceil(max(0, timeout) * 1000)
        events = self.poller.poll(milliseconds)
        for _, flags in events:
            if flags & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                raise OSError(errno.ENODEV, 'Wi-Fi request channel unavailable')
        return bool(events)


def signal_payload(state):
    if not state.get('locationAuthorized') or not state.get('networkIdentityAvailable'):
        return None
    value = state.get('signalDBm')
    if type(value) is not int or not -127 <= value <= 0:
        raise ValueError('Invalid host signal')
    address = state.get('bssid')
    if not isinstance(address, str):
        raise ValueError('Missing host BSSID')
    address = bytes.fromhex(address.replace(':', ''))
    if len(address) != 6 or address == b'\0' * 6 or address[0] & 1:
        raise ValueError('Invalid host BSSID')
    return struct.pack('<6sbB', address, value, 0)


def handle_connection(payload, rpc, publish=None):
    number, operation, ssid_len, key_len, reserved, bssid, ssid, key = CONNECTION.unpack(payload)
    if not number or operation not in (1, 2) or reserved:
        raise ValueError('Invalid kernel connection request')
    if operation == 2:
        rpc({'action': 'wifi-disconnect'})
        state = rpc({'action': 'wifi-status'})
        if not state.get('locationAuthorized') or state.get('networkIdentityAvailable'):
            raise RuntimeError('Host disconnection not confirmed')
        return b'\0' * 6
    if not 1 <= ssid_len <= 32 or key_len not in (0, 32):
        raise ValueError('Invalid network/key size')
    target = ssid[:ssid_len]
    records = scan(rpc)
    candidates = [record for record in records if record.ssid == target and
                  (bssid == b'\0' * 6 or record.bssid == bssid) and compatible(record, bool(key_len))]
    if not candidates:
        raise RuntimeError('Selected network absent from current host scan')
    selected = max(candidates, key=lambda record: record.signal_mbm)
    request = {'action': 'wifi-connect', 'networkID': selected.network_id}
    if key_len:
        request['password'] = key.hex()
    try:
        rpc(request)
    finally:
        request.clear()
    state = rpc({'action': 'wifi-status'})
    if not state.get('locationAuthorized') or base64.b64decode(state.get('ssidBase64', ''), validate=True) != target:
        raise RuntimeError('Host association not confirmed')
    actual = bytes.fromhex(state.get('bssid', '').replace(':', ''))
    if len(actual) != 6 or not any(actual) or actual[0] & 1:
        raise RuntimeError('Host omitted valid associated BSSID')
    if bssid != b'\0' * 6 and actual != bssid:
        raise RuntimeError('Host associated to a different requested BSSID')
    if not any(r.ssid == target and r.bssid == actual for r in records):
        records = scan(rpc)
        if identity(rpc({'action': 'wifi-status'})) != (target, actual):
            raise RuntimeError('Host changed association during verification')
    confirmed = actual_bss(records, target, actual, bool(key_len))
    if publish: publish(number, confirmed)
    return actual


def serve(path, once=False):
    def rpc(request, timeout=85):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(timeout)
            client.connect(path)
            client.sendall(json.dumps(request).encode() + b'\n')
            line = client.makefile('rb').readline(16384)
            if not line.endswith(b'\n'):
                raise ValueError('Truncated host response')
            reply = json.loads(line)
            if not reply.get('ok'):
                raise RuntimeError(reply.get('error', 'Host scan failed'))
            return reply

    with open('/dev/linuxhost-wifi', 'wb', buffering=0) as device, contextlib.ExitStack() as cleanup:
        wait = RequestWait(device)
        follower = LinkFollower(device) if wait.capabilities & CAP_HOST_LINK else None
        watcher = StatusWatch() if follower and not once else None
        if watcher: cleanup.callback(watcher.close)
        if follower: print('Wi-Fi association: host BSS selection and link reports', flush=True)
        previous = 0
        next_signal = 0
        link_error = None
        while True:
            if watcher:
                watcher.open()
                if watcher.changed(): next_signal = 0
                wait.watch(watcher)
            connection = bytearray(CONNECTION.size)
            fcntl.ioctl(device, GET_CONNECTION, connection)
            op_sequence = struct.unpack_from('<I', connection)[0]
            if op_sequence:
                status_code, address = 1, b'\0' * 6
                try:
                    publish = (lambda sequence, bss: report(device, sequence, 2, bss)) if follower else None
                    address = handle_connection(connection, rpc, publish)
                    status_code = 0
                except Exception as error:
                    # RuntimeError text here is a fixed bridge/backend diagnostic,
                    # never the request, SSID or key. Other exception strings
                    # may contain data, so retain only their type.
                    detail = ': ' + str(error) if isinstance(error, RuntimeError) else ''
                    print('Connection bridge failed: ' + type(error).__name__ + detail, flush=True)
                finally:
                    connection[:] = b'\0' * len(connection)
                try:
                    fcntl.ioctl(device, CONNECTION_RESULT,
                                struct.pack('<IHH6s2x', op_sequence, status_code, 0, address))
                except OSError:
                    pass  # Kernel cancelled/timed out while host was working.
                print(json.dumps({'connection_sequence': op_sequence, 'host_operation_ok': status_code == 0}), flush=True)
                next_signal = 0
                if once:
                    if status_code:
                        raise SystemExit(1)
                    return
                continue
            sequence = bytearray(4)
            fcntl.ioctl(device, GET_SCAN, sequence)
            number = struct.unpack('=I', sequence)[0]
            if not number or number == previous:
                if not once and time.monotonic() >= next_signal:
                    next_signal = time.monotonic() + 5
                    try:
                        link = follower.snapshot() if follower else None
                        active = not watcher or watcher.key is None or watcher.key[1] is not False
                        if active and (link is None or link[1] & 1 and not link[1] & 8):
                            state = rpc({'action': 'wifi-status'}, timeout=2)
                            if follower:
                                follower.update(link, state, rpc)
                                if follower.missing: next_signal = min(next_signal, follower.missing[1])
                            payload = signal_payload(state)
                            if payload is not None: fcntl.ioctl(device, SIGNAL, payload)
                            if link_error:
                                print('Wi-Fi host link update recovered', flush=True); link_error = None
                    except (OSError, ValueError, RuntimeError) as error:
                        # New kernel work can supersede a bounded host request.
                        # Its generation wins; do not apply a delayed link state.
                        if follower and not (isinstance(error, OSError) and error.errno == errno.ESTALE):
                            kind = type(error).__name__
                            if kind != link_error:
                                print('Wi-Fi host link update unavailable:', kind, flush=True); link_error = kind
                        # The kernel expires stale signal; never invent a sample.
                # Status changes also wake this wait. The five-second signal
                # deadline remains a compatibility/recovery check for the link.
                wait.wait(None if once else next_signal - time.monotonic())
                continue
            previous = number
            try:
                records = scan(rpc)
                for bss in records:
                    # ESS and privacy describe the host's actual BSS. Other
                    # capabilities are not invented. Beacon interval unknown.
                    capability = 1 | (0 if bss.open else 0x10)
                    header = struct.pack('<IIiHH6sH', number, bss.frequency_mhz,
                                         bss.signal_mbm, capability, 0,
                                         bss.bssid, len(bss.information_elements))
                    packet = header + bss.information_elements
                    if os.write(device.fileno(), packet) != len(packet):
                        raise RuntimeError('Incomplete kernel record write')
                fcntl.ioctl(device, FINISH_SCAN, sequence)
                print(json.dumps({'published_bss_count': len(records), 'scan_sequence': number}), flush=True)
            except Exception as error:
                # Abort this exact scan promptly. A missing/crashed bridge still
                # has the kernel's independent timeout as a backstop.
                try:
                    fcntl.ioctl(device, ABORT_SCAN, sequence)
                except OSError:
                    pass  # Request already timed out or device was removed.
                print('Scan bridge failed: ' + type(error).__name__, flush=True)
                if once:
                    raise
            if once:
                return


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', default='/run/linuxhost/agent.sock')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    serve(args.socket, args.once)
