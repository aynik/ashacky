#!/usr/bin/env python3
"""Management-only host Bluetooth client. Never creates a guest input device."""
import argparse
import json
import socket
import time

DEFAULT_SOCKET = '/run/linuxhost-bluetooth-test.sock'


def request(payload, path=DEFAULT_SOCKET):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(18)
        sock.connect(path)
        sock.sendall(json.dumps(payload).encode() + b'\n')
        with sock.makefile('rb') as stream:
            line = stream.readline(16385)
    if len(line) > 16384 or not line.endswith(b'\n'):
        raise RuntimeError('Invalid host response framing')
    reply = json.loads(line)
    if not isinstance(reply, dict) or reply.get('ok') is not True:
        raise RuntimeError(reply.get('error', 'Host operation failed') if isinstance(reply, dict) else 'Invalid host response')
    return reply


def device_operation(address, action, path=DEFAULT_SOCKET, confirm=None, display=None):
    if action not in ("bluetooth-connect", "bluetooth-disconnect", "bluetooth-pair"):
        raise ValueError("Unsupported device operation")
    reply = request({"action": action, "address": address}, path)
    operation_id = reply['operation']['id']
    deadline = time.monotonic() + (75 if action == "bluetooth-pair" else 45)
    displayed = None
    while time.monotonic() < deadline:
        state = request({'action': 'bluetooth-devices'}, path)
        operation = state.get('operation', {})
        if operation.get('id') != operation_id:
            raise RuntimeError('Operation was superseded; inspect current device state')
        if 'confirmation' in operation:
            code = operation['confirmation']
            if type(code) is not int or not 0 <= code <= 999999:
                raise RuntimeError('Invalid host pairing code')
            accepted = False
            try:
                accepted = confirm(code) is True if confirm else False
            finally:
                request({'action': 'bluetooth-confirm', 'operationID': operation_id, 'accepted': accepted}, path)
        if 'passkey' in operation and operation['passkey'] != displayed:
            code = operation['passkey']
            if type(code) is not int or not 0 <= code <= 999999:
                raise RuntimeError('Invalid host passkey')
            if display: display(code)
            displayed = code
        if operation.get('state') == 'complete':
            if operation.get('success') is not True:
                raise RuntimeError('Host device operation failed: ' + str(operation.get('result')))
            return operation
        time.sleep(0.5)
    # A client deadline does not cancel or restart an in-flight host operation.
    raise TimeoutError('Host operation still pending; inspect current device state before retrying')


def change_connection(address, connect, path=DEFAULT_SOCKET):
    return device_operation(address, "bluetooth-connect" if connect else "bluetooth-disconnect", path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default=DEFAULT_SOCKET)
    parser.add_argument('action', choices=['list', 'scan', 'connect', 'disconnect', 'pair'])
    parser.add_argument('address', nargs='?')
    args = parser.parse_args()
    if args.action in ('connect', 'disconnect', 'pair'):
        if args.address is None:
            parser.error('a device address is required')
        result = device_operation(args.address, 'bluetooth-' + args.action, args.socket)
    else:
        result = request({'action': 'bluetooth-devices' if args.action == 'list' else 'bluetooth-scan'}, args.socket)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
