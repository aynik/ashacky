#!/usr/bin/env python3
"""List or select the Mac audio endpoint used by the VM's existing audio path."""
import argparse
import json
from bluetooth_management import DEFAULT_SOCKET, request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default=DEFAULT_SOCKET)
    parser.add_argument('action', choices=['list', 'input', 'output'])
    parser.add_argument('uid', nargs='?')
    args = parser.parse_args()
    if args.action == 'list':
        if args.uid is not None:
            parser.error('list takes no device UID')
        payload = {'action': 'audio-devices'}
    else:
        if not args.uid:
            parser.error('select a device UID from list')
        payload = {'action': 'audio-select', 'direction': args.action, 'uid': args.uid}
    print(json.dumps(request(payload, args.socket), indent=2))


if __name__ == '__main__':
    main()
