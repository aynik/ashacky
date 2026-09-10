import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('sidecar_agent', str(ROOT / 'guest/libexec/linuxhost-agent'))
spec = importlib.util.spec_from_loader(loader.name, loader)
agent = importlib.util.module_from_spec(spec)
loader.exec_module(agent)
TARGET = 'sidecar:' + 'a' * 64


class SidecarTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('gjs'), 'GNOME GJS runtime unavailable')
    def test_menu_request_lifecycle(self):
        subprocess.run(['gjs', '-m', str(ROOT / 'tests/gnome-displays.js')], check=True, timeout=10)

    def test_desktop_authorization_and_argument_allowlist(self):
        for uid in (0, 1000):
            for action in ('display-list', 'display-connect', 'display-disconnect'):
                result = agent.authorize(dict(action=action, id=TARGET, token='spoof', command='sh'), uid, 1000)
                self.assertEqual(result, dict(action=action, **({} if action == 'display-list' else {'id': TARGET})))
        with self.assertRaises(PermissionError):
            agent.authorize(dict(action='display-list'), 1001, 1000)

    def test_invalid_targets_are_never_forwarded(self):
        for target in (None, 0, [], '', TARGET + '\n', TARGET.upper(), 'iPad', '../helper', TARGET + 'x'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                agent.authorize(dict(action='display-connect', id=target), 1000, 1000)
        with self.assertRaises(ValueError):
            agent.authorize(dict(action='display-exec', id=TARGET), 1000, 1000)

    def test_slow_display_does_not_block_lock_and_workers_are_bounded(self):
        entered, release = threading.Event(), threading.Event()
        def request(value):
            if value['action'] == 'display-connect':
                entered.set()
                self.assertTrue(release.wait(3))
            return {'ok': True, 'action': value['action']}
        pairs = []
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'userID': os.getuid()}))
            with patch.object(agent, 'CONFIG', config), patch.object(agent, 'host_request', side_effect=request):
                workers = agent.ClientWorkers(limit=2)
                try:
                    for _ in range(3):
                        pair = socket.socketpair(); pairs.append(pair)
                        pair[1].settimeout(2)
                    self.assertTrue(workers.start(pairs[0][0]))
                    pairs[0][1].sendall(json.dumps(dict(action='display-connect', id=TARGET)).encode() + b'\n')
                    self.assertTrue(entered.wait(1))
                    # Occupy the second worker before sending its request.
                    self.assertTrue(workers.start(pairs[1][0]))
                    self.assertFalse(workers.start(pairs[2][0]))
                    self.assertEqual(pairs[2][1].recv(1), b'')
                    pairs[1][1].sendall(b'{"action":"lock"}\n')
                    self.assertEqual(json.loads(pairs[1][1].recv(4096))['action'], 'lock')
                    release.set()
                    self.assertEqual(json.loads(pairs[0][1].recv(4096))['action'], 'display-connect')
                finally:
                    release.set()
                    for pair in pairs:
                        for sock in pair: sock.close()


if __name__ == '__main__':
    unittest.main()
