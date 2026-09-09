#!/usr/bin/env python3
"""Connect the real Swift/Python protocol engines using private socket pairs."""
import argparse
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import selectors
import socket
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--peer', type=Path, required=True)
    args = parser.parse_args()
    loader = importlib.machinery.SourceFileLoader('control_check', str(ROOT / 'guest/libexec/ashacky-control'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    port, peer = socket.socketpair()
    port.setblocking(False)
    process = subprocess.Popen([str(args.peer)], stdin=peer, stdout=peer)
    peer.close()
    watchdog = threading.Timer(8, process.kill); watchdog.start()
    broker = module.Broker(port.fileno(), 'x' * 32)
    clients = []
    try:
        while broker.session is None:
            broker.step()
        for service in ('wifi', 'bluetooth'):
            client, desktop = socket.socketpair()
            client.setblocking(False); desktop.setblocking(False)
            broker.clients[client] = dict(service=service, frames=module.Frames(),
                deadline=time.monotonic() + 5, output=bytearray(), id=None)
            broker.selector.register(client, selectors.EVENT_READ, 'client')
            desktop.sendall(b'{"action":"fixture","sequence":42}\n')
            clients.append(desktop)
        received = {}
        order = []
        while len(received) < 2:
            broker.step()
            for index, client in enumerate(clients):
                if index in received:
                    continue
                try:
                    data = client.recv(65536)
                except BlockingIOError:
                    continue
                received[index] = json.loads(data); order.append(index)
        assert order == [1, 0], order
        assert received[0] == dict(ok=True, service='wifi', echo=dict(action='fixture', sequence=42))
        assert received[1]['service'] == 'bluetooth'
        print('Swift/Python handshake, parallel RPC correlation passed')
    finally:
        broker.close(); port.close()
        for client in clients:
            client.close()
        process.wait(timeout=3)
        watchdog.cancel()
    assert process.returncode == 0


if __name__ == '__main__':
    main()
