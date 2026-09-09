#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""CTAPHID/UHID transport for the private host FIDO2 authenticator.

No keys, passwords, CBOR policy or cryptographic signing live in this process.
Idle waits are descriptor-driven; timers exist only for in-flight transactions.
"""
import base64
import collections
import errno
import fcntl
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import socket
import struct
import sys
import time
import uuid

# CTAP2's baseline message capacity. Keeping a complete transaction below the
# kernel UHID output queue capacity also bounds unacknowledged report bursts.
MAX_MESSAGE = 1024
BROADCAST = 0xffffffff
PING, INIT, CBOR, CANCEL, KEEPALIVE, ERROR = 0x81, 0x86, 0x90, 0x91, 0xbb, 0xbf
REPORT = bytes.fromhex('06d0f10901a1010920150026ff007508954081020921150026ff00750895409102c0')
ENDPOINT = '/run/ashacky-control/fido2.sock'


def packets(channel, command, payload):
    if len(payload) > MAX_MESSAGE:
        raise ValueError('CTAPHID payload exceeds transport limit')
    first = struct.pack('>IBH', channel, command, len(payload)) + payload[:57]
    yield first.ljust(64, b'\0')
    for sequence, offset in enumerate(range(57, len(payload), 59)):
        yield (struct.pack('>IB', channel, sequence) + payload[offset:offset + 59]).ljust(64, b'\0')


class HID:
    def __init__(self, emit, request, cancel, clock=time.monotonic):
        self.emit, self.request, self.cancel, self.clock = emit, request, cancel, clock
        self.channels = collections.OrderedDict()
        self.fragment = None
        self.busy = None

    def send(self, cid, cmd, payload):
        for packet in packets(cid, cmd, payload):
            self.emit(packet)

    def error(self, cid, code):
        self.send(cid, ERROR, bytes([code]))

    def abort(self, respond=False):
        self.fragment = None
        if self.busy:
            cid, request_id, _, _ = self.busy
            self.busy = None
            self.cancel(request_id)
            if respond:
                self.send(cid, CBOR, b'\x2d')

    def receive(self, packet):
        if len(packet) != 64:
            return
        cid, cmd = struct.unpack('>IB', packet[:5])
        if cid == 0:
            self.error(cid, 0x0b); return
        if cmd == INIT:
            length = struct.unpack('>H', packet[5:7])[0]
            if length != 8:
                self.error(cid, 0x03); return
            if cid != BROADCAST and cid not in self.channels:
                self.error(cid, 0x0b); return
            assigned = cid
            if cid == BROADCAST:
                while assigned in (0, BROADCAST) or assigned in self.channels:
                    assigned = secrets.randbits(32)
                if len(self.channels) >= 64:
                    protected = {self.busy[0]} if self.busy else set()
                    if self.fragment:
                        protected.add(self.fragment['cid'])
                    victim = next((c for c in self.channels if c not in protected), None)
                    if victim is not None:
                        self.channels.pop(victim)
                self.channels[assigned] = None
            else:
                if self.busy and self.busy[0] == cid:
                    self.abort()
                if self.fragment and self.fragment['cid'] == cid:
                    self.fragment = None
            # CTAPHID v2; CBOR and NMSG capabilities. No legacy U2F/WINK/PIN claim.
            self.send(cid, INIT, packet[7:15] + struct.pack('>IBBBBB', assigned, 2, 0, 1, 0, 0x0c))
            return
        if cid == BROADCAST or cid not in self.channels:
            self.error(cid, 0x0b); return
        self.channels.move_to_end(cid)
        if cmd == CANCEL:
            if packet[5:7] != b'\0\0':
                self.error(cid, 0x03)
            elif self.busy and self.busy[0] == cid:
                self.abort(respond=True)
            return
        if self.busy:
            self.error(cid, 0x06); return
        if cmd & 0x80:
            if self.fragment:
                if self.fragment['cid'] != cid:
                    self.error(cid, 0x06)
                else:
                    self.fragment = None
                    self.error(cid, 0x04)
                return
            length = struct.unpack('>H', packet[5:7])[0]
            if length > MAX_MESSAGE:
                self.error(cid, 0x03); return
            if cmd not in (PING, CBOR):
                self.error(cid, 0x01); return
            self.fragment = dict(cid=cid, cmd=cmd, length=length, data=bytearray(packet[7:7 + min(length, 57)]),
                                 sequence=0, deadline=self.clock() + 3)
        else:
            part = self.fragment
            if part is None:
                return
            if part['cid'] != cid:
                self.error(cid, 0x06); return
            if cmd != part['sequence']:
                self.fragment = None; self.error(cid, 0x04); return
            part['sequence'] += 1
            part['data'].extend(packet[5:5 + min(59, part['length'] - len(part['data']))])
        part = self.fragment
        if part and len(part['data']) == part['length']:
            self.fragment = None
            payload = bytes(part['data'])
            if part['cmd'] == PING:
                self.send(cid, PING, payload)
            elif not payload:
                self.error(cid, 0x03)
            else:
                request_id = str(uuid.uuid4())
                self.busy = (cid, request_id, self.clock() + 65, self.clock() + 0.2)
                self.request(request_id, cid, payload)

    def complete(self, request_id, payload):
        if self.busy and self.busy[1] == request_id:
            cid = self.busy[0]
            self.busy = None
            self.send(cid, CBOR, payload if 1 <= len(payload) <= MAX_MESSAGE else b'\x7f')

    def timeout(self):
        now = self.clock()
        if self.fragment and now >= self.fragment['deadline']:
            cid = self.fragment['cid']; self.fragment = None
            self.error(cid, 0x05)
        if self.busy:
            cid, request_id, deadline, keepalive = self.busy
            if now >= deadline:
                self.busy = None; self.cancel(request_id)
                self.send(cid, CBOR, b'\x2f')
            elif now >= keepalive:
                self.send(cid, KEEPALIVE, b'\x02')
                self.busy = (cid, request_id, deadline, now + 0.2)

    def deadline(self):
        times = []
        if self.fragment:
            times.append(self.fragment['deadline'])
        if self.busy:
            times.extend(self.busy[2:])
        return max(0, min(times) - self.clock()) if times else None


class Device:
    def __init__(self, unique='org.ashacky.fido2'):
        self.fd = os.open('/dev/uhid', os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        header = struct.pack('<I128s64s64sHHIIII', 11, b'Ashacky FIDO2', b'ashacky/fido2', unique.encode(),
                             len(REPORT), 3, 0xffff, 0xa5c2, 1, 0)
        self.write(header + REPORT)

    def write(self, event):
        if os.write(self.fd, event) != len(event):
            raise OSError('Incomplete UHID event')

    def emit(self, packet):
        self.write(struct.pack('<IH', 12, len(packet)) + packet)

    def receive(self, hid):
        event = os.read(self.fd, 4380)
        if len(event) < 4:
            raise EOFError('UHID closed')
        event = event.ljust(4380, b'\0')
        kind = struct.unpack_from('<I', event)[0]
        if kind == 6:  # UHID_OUTPUT
            length, report_type = struct.unpack_from('<HB', event, 4100)
            if report_type == 1:
                if length == 64:
                    hid.receive(event[4:68])
                elif length == 65 and event[4] == 0:
                    # hidraw writes can retain the unnumbered report-ID byte.
                    hid.receive(event[5:69])
        elif kind in (3, 5):  # STOP / last reader CLOSE
            hid.abort()
        elif kind == 9:  # GET_REPORT unsupported (interrupt reports only)
            request_id = struct.unpack_from('<I', event, 4)[0]
            self.write(struct.pack('<IIHH', 10, request_id, errno.EOPNOTSUPP, 0))
        elif kind == 13:  # SET_REPORT unsupported
            request_id = struct.unpack_from('<I', event, 4)[0]
            self.write(struct.pack('<IIH', 14, request_id, errno.EOPNOTSUPP))

    def close(self):
        os.close(self.fd)  # UHID destroys only the device owned by this descriptor.


class Relay:
    def __init__(self, device, endpoint=ENDPOINT):
        self.device, self.endpoint = device, endpoint
        self.selector = selectors.DefaultSelector()
        self.selector.register(device.fd, selectors.EVENT_READ, 'device')
        self.clients = {}
        self.hid = HID(device.emit, self.request, self.cancel)

    def rpc(self, value, completion):
        if len(self.clients) >= 8:
            completion(None); return
        client = socket.socket(socket.AF_UNIX)
        client.setblocking(False)
        try:
            error = client.connect_ex(self.endpoint)
            if error not in (0, errno.EINPROGRESS, errno.EAGAIN):
                raise OSError(error, 'Control service unavailable')
            self.clients[client] = dict(output=bytearray(json.dumps(value, separators=(',', ':')).encode() + b'\n'),
                input=bytearray(), completion=completion, deadline=time.monotonic() + 70)
            self.selector.register(client, selectors.EVENT_WRITE, 'rpc')
        except OSError:
            client.close(); completion(None)

    def finish(self, client, result):
        record = self.clients.pop(client)
        self.selector.unregister(client); client.close()
        record['completion'](result)

    def request(self, request_id, channel, payload):
        def complete(result):
            try:
                if not isinstance(result, dict) or result.get('ok') is not True:
                    raise ValueError('Host unavailable')
                raw = base64.b64decode(result['data'], validate=True)
            except (KeyError, TypeError, ValueError):
                raw = b'\x7f'
            self.hid.complete(request_id, raw)
        self.rpc(dict(action='request', id=request_id, channel=f'{channel:08x}',
                      data=base64.b64encode(payload).decode()), complete)

    def cancel(self, request_id):
        self.rpc(dict(action='cancel', id=request_id), lambda _: None)

    def step(self):
        timeout = self.hid.deadline()
        deadlines = [r['deadline'] for r in self.clients.values()]
        if deadlines:
            next_rpc = max(0, min(deadlines) - time.monotonic())
            timeout = next_rpc if timeout is None else min(timeout, next_rpc)
        for key, events in self.selector.select(timeout):
            if key.data == 'device':
                self.device.receive(self.hid)
                continue
            client = key.fileobj
            record = self.clients[client]
            try:
                if events & selectors.EVENT_WRITE:
                    count = client.send(record['output']); del record['output'][:count]
                    if not record['output']:
                        self.selector.modify(client, selectors.EVENT_READ, 'rpc')
                if events & selectors.EVENT_READ:
                    data = client.recv(16384)
                    if not data:
                        raise EOFError('Host closed')
                    record['input'].extend(data)
                    if len(record['input']) > 16384:
                        raise ValueError('Oversized reply')
                    if b'\n' in record['input']:
                        self.finish(client, json.loads(record['input']))
            except (OSError, ValueError, EOFError):
                if client in self.clients:
                    self.finish(client, None)
        self.hid.timeout()
        now = time.monotonic()
        for client, record in list(self.clients.items()):
            if now >= record['deadline']:
                self.finish(client, None)

    def close(self):
        pending = self.hid.busy[1] if self.hid.busy else None
        self.hid.abort()
        for client in list(self.clients):
            self.selector.unregister(client); client.close()
        self.clients.clear(); self.selector.close(); self.device.close()
        if pending:
            # Flush cancellation on graceful service stop. No authentication is
            # retried; an unavailable host retains its own native prompt deadline.
            try:
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(1)
                    client.connect(self.endpoint)
                    client.sendall(json.dumps(dict(action='cancel', id=pending)).encode() + b'\n')
                    client.recv(4096)
            except OSError:
                pass


def check_host(endpoint=ENDPOINT):
    """One startup check, before publishing a device. Never asks for approval."""
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10)
        client.connect(endpoint)
        request = dict(action='request', id=str(uuid.uuid4()), channel='00000001', data='BA==')
        client.sendall(json.dumps(request).encode() + b'\n')
        data = bytearray()
        while b'\n' not in data:
            part = client.recv(4096)
            if not part or len(data) + len(part) > 16384:
                raise OSError('Invalid FIDO2 host response')
            data.extend(part)
        result = json.loads(data)
        if result.get('ok') is not True:
            if result.get('error') == 'FIDO2 disabled':
                print('FIDO2 is not enabled on the host', file=sys.stderr)
                raise SystemExit(78)
            raise OSError('FIDO2 host is unavailable')
        raw = base64.b64decode(result['data'], validate=True)
        if len(raw) < 2 or raw[0] != 0:
            raise OSError('FIDO2 host is not ready')


def main():
    if os.geteuid() != 0:
        raise PermissionError('The FIDO2 transport must run as root')
    # The service is explicitly enabled after the host prototype is accepted.
    lock = os.open('/run/ashacky-fido2/transport.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    check_host()
    relay = Relay(Device())
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        while True:
            relay.step()
    finally:
        relay.close(); os.close(lock)


if __name__ == '__main__':
    main()
