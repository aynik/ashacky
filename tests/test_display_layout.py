import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DisplayLayoutTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler unavailable')
    def test_hotplug_policy_and_qemu_capacity(self):
        # This is the planner used by the frontend and Sidecar helper. No host
        # screens, desktop windows, QEMU or private Apple framework are opened.
        spec = importlib.util.spec_from_file_location('display_session', ROOT / 'host/session/session.py')
        session = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(session)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'display-layout'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(ROOT / 'tests/display-layout.c'), '-o', str(executable)], check=True)
            capacity = int(subprocess.check_output([str(executable)], text=True, timeout=5))
            config = {'runtime': directory, 'uuid': '00000000-0000-4000-8000-000000000001',
                      'macs': [], 'vars': directory + '/vars', 'disk': directory + '/disk',
                      'shared': directory + '/shared', 'diskSerial': 'TESTDISK',
                      'usbSocket': directory + '/usb.sock'}
            gpu = next(arg for arg in session.arguments(config, []) if arg.startswith('virtio-ramfb-gl,'))
            properties = dict(part.split('=', 1) for part in gpu.split(',')[1:])
            self.assertEqual(int(properties['max_outputs']), capacity)
            self.assertGreaterEqual(capacity, 3)
