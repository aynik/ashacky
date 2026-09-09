import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('lock_agent', str(ROOT / 'guest/libexec/linuxhost-agent'))
spec = importlib.util.spec_from_loader(loader.name, loader)
agent = importlib.util.module_from_spec(spec)
loader.exec_module(agent)


class HostLockTests(unittest.TestCase):
    def test_only_root_or_selected_desktop_can_request_host_lock(self):
        request = {'action': 'lock', 'token': 'untrusted', 'command': 'sh', 'userID': 9999}
        for uid in (0, 1000):
            self.assertEqual(agent.authorize(request, uid, 1000), {'action': 'lock'})
        with self.assertRaises(PermissionError):
            agent.authorize(request, 1001, 1000)

    def test_no_unlock_shell_or_authentication_bypass(self):
        for action in ('unlock', 'exec', 'vm-stopped'):
            with self.assertRaises(ValueError):
                agent.authorize({'action': action}, 1000, 1000)
        for purpose in ('sudo', 'unlock'):
            with self.assertRaises(PermissionError):
                agent.authorize({'action': 'authenticate', 'purpose': purpose}, 1000, 1000)
            self.assertEqual(agent.authorize({'action': 'authenticate', 'purpose': purpose}, 0, 1000),
                             {'action': 'authenticate', 'purpose': purpose})

    @unittest.skipUnless(shutil.which('gjs'), 'GNOME GJS runtime unavailable')
    def test_gnome_lock_routes_without_opening_guest_shield(self):
        subprocess.run(['gjs', '-m', str(ROOT / 'tests/gnome-host-lock.js')], check=True, timeout=10)
