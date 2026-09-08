#!/usr/bin/env python3
"""Bounded desktop access to host audio management; no arbitrary RPC forwarding."""
import json
import os
import re
from pathlib import Path
import socket
import struct
from bluetooth_management import request

ENDPOINT = Path('/run/linuxhost-devices/control.sock')


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid request')
    if value in ({"action": "audio-devices"}, {"action": "bluetooth-devices"}, {"action": "bluetooth-scan"}):
        return value
    if set(value) == {'action', 'direction', 'uid'} and value['action'] == 'audio-select':
        if value['direction'] not in ('input', 'output'):
            raise ValueError('Invalid direction')
        uid = value['uid']
        if not isinstance(uid, str) or not 0 < len(uid.encode()) <= 1024:
            raise ValueError('Invalid device UID')
        return value
    if set(value) == {'action', 'address'} and value['action'] in ('bluetooth-pair', 'bluetooth-connect', 'bluetooth-disconnect'):
        if isinstance(value['address'], str) and re.fullmatch(r'[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}', value['address']):
            return value
        raise ValueError('Invalid Bluetooth address')
    if set(value) == {'action', 'operationID', 'accepted'} and value['action'] == 'bluetooth-confirm':
        if isinstance(value['operationID'], str) and re.fullmatch(r'[0-9A-Fa-f-]{36}', value['operationID']) and type(value['accepted']) is bool:
            return value
        raise ValueError('Invalid pairing confirmation')
    raise ValueError('Unsupported request')


def serve():
    ENDPOINT.parent.mkdir(mode=0o755, exist_ok=True)
    ENDPOINT.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(ENDPOINT))
        os.chmod(ENDPOINT, 0o666)  # SO_PEERCRED below restricts every operation.
        server.listen(4)
        while True:
            client, _ = server.accept()
            with client:
                client.settimeout(20)
                try:
                    _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid not in (0, json.loads(Path('/etc/linuxhost.json').read_text())['userID']):
                        raise PermissionError('Desktop user required')
                    line = client.makefile('rb').readline(4097)
                    if len(line) > 4096 or not line.endswith(b'\n'):
                        raise ValueError('Invalid framing')
                    result = request(validate(json.loads(line)))
                except Exception as error:
                    result = {'ok': False, 'error': str(error)}
                try:
                    client.sendall(json.dumps(result).encode() + b'\n')
                except OSError:
                    pass


if __name__ == '__main__':
    serve()
