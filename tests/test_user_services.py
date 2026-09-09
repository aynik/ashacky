import importlib.util
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('user_services', ROOT / 'host/session/user_services.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class UserServicesTests(unittest.TestCase):
    def test_children_are_isolated_restarted_and_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            services = module.UserServices({'worker': [sys.executable, '-c', 'import time; time.sleep(60)']},
                                           dict(os.environ), directory)
            try:
                services.start_all()
                old = services.processes['worker']
                self.assertEqual(os.getpgid(old.pid), old.pid)
                old.kill(); old.wait()
                services.poll()
                new = services.processes['worker']
                self.assertNotEqual(old.pid, new.pid)
                self.assertIsNone(new.poll())
            finally:
                services.stop()
            self.assertIsNotNone(new.poll())
            self.assertFalse(services.processes)

    def test_repeated_failures_have_a_restart_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            services = module.UserServices({'worker': [sys.executable, '-c', 'raise SystemExit(2)']},
                                           dict(os.environ), directory)
            try:
                services.start_all()
                for clock in (100, 111, 122):
                    services.processes['worker'].wait()
                    with patch.object(module.time, 'monotonic', return_value=clock):
                        services.poll()
                failed = services.processes['worker']; failed.wait()
                with patch.object(module.time, 'monotonic', return_value=133):
                    services.poll()
                self.assertIs(services.processes['worker'], failed)
                with patch.object(module.time, 'monotonic', return_value=172):
                    services.poll()
                self.assertIsNot(services.processes['worker'], failed)
            finally:
                services.stop()
