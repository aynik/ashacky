import importlib.util
import json
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('planner', ROOT / 'tools/plan-installation.py')
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


class InstallationPlanTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'examples/installation.json').read_text())

    def test_offline_plan_has_one_identity_and_preserves_virtual_wifi_management(self):
        with tempfile.TemporaryDirectory() as directory:
            output = planner.generate(self.config, Path(directory) / 'plan')
            def read(name):
                return json.loads((output / name).read_text())
            vm = read('host/private/vm.json')
            control = read('host/private/control.json')
            guest = read('guest/etc/linuxhost.json')
            devices = read('guest/etc/ashacky/devices.json')
            self.assertEqual(control['token'], guest['token'])
            self.assertEqual(vm['uuid'], devices['vmUUID'])
            self.assertEqual(vm['macs'][1], devices['wifiMAC'])
            self.assertNotEqual(vm['userID'], guest['userID'])
            self.assertFalse(control['powerEnabled'])
            nm = (output / 'guest/etc/NetworkManager/conf.d/90-ashacky-transport.conf').read_text()
            self.assertNotIn('mac:', nm)
            self.assertIn('interface-name:' + devices['wifiInterface'], nm)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((output / 'host/private/control.json').stat().st_mode), 0o600)
            # Logout must not immediately respawn a new VM via KeepAlive.
            session = next((output / 'host/LaunchAgents').glob('*.session.plist'))
            self.assertNotIn('KeepAlive', plistlib.loads(session.read_bytes()))
            # The frontend owns permission services; no second app can race its listeners.
            jobs = [plistlib.loads(path.read_bytes()) for path in (output / 'host/LaunchAgents').glob('*.plist')]
            self.assertEqual({job['Label'].rsplit('.', 1)[-1] for job in jobs},
                             {'session'})
            for job in jobs:
                self.assertEqual(job['AssociatedBundleIdentifiers'], ['local.ashacky.host'])
            daemons = [plistlib.loads(path.read_bytes()) for path in (output / 'host/LaunchDaemons').glob('*.plist')]
            for job in daemons:
                self.assertTrue(job['ProgramArguments'][0].endswith('/service.sh'))
                self.assertEqual(job['AssociatedBundleIdentifiers'], ['local.ashacky.host'])
                self.assertTrue(job['StandardErrorPath'].startswith('/var/log/ashacky-'))
            with self.assertRaises(FileExistsError):
                planner.generate(self.config, output)

    def test_invalid_paths_and_identities_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'plan'
            for key, value in [('hostUID', 0), ('guestUID', True), ('checkout', '/tmp/a,b'),
                               ('privateDirectory', '/tmp/../live'), ('guestWifiInterface', '../bad')]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    planner.generate(dict(self.config, **{key: value}), output)
                self.assertFalse(output.exists())

    def test_unusable_subnet_and_unix_socket_length_are_rejected(self):
        for update in ({'hostAddress': '192.168.90.2'}, {'managementSubnet': '192.168.90.0/25'},
                       {'privateDirectory': '/tmp/' + 'a' * 120},
                       {'guestWifiInterface': self.config['guestManagementInterface']}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                planner.validate(dict(self.config, **update))
