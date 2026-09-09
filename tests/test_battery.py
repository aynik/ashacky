import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(name, loader)
    result = importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result


agent = module('agent', 'guest/libexec/linuxhost-agent')
feed = module('feed', 'guest/drivers/battery/feed.py')


def status(online=True):
    return {'ok': True, 'wakeGeneration': 0, 'battery': {
        'Current Capacity': 42, 'Max Capacity': 100, 'Is Charging': online,
        'Power Source State': 'AC Power' if online else 'Battery Power'}}


class BatteryTests(unittest.TestCase):
    def test_stream_handles_multiple_messages_and_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'port'
            # Force a message to span reads, followed by another in the same stream.
            values = [dict(status(), padding='x' * 5000), status(False)]
            path.write_text(''.join(json.dumps({'version': 1, 'status': s}) + '\n' for s in values))
            stream = agent.status_stream(path)
            self.assertEqual(next(stream), values[0])
            self.assertEqual(next(stream), values[1])
            with self.assertRaises(EOFError):
                next(stream)

    def test_stream_rejects_invalid_version_and_unbounded_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'port'
            for value in ('x' * 17000, 'x' * 17000 + '\n',
                          json.dumps({'version': True, 'status': status()}) + '\n',
                          json.dumps({'version': 2, 'status': status()}) + '\n'):
                path.write_text(value)
                with self.subTest(size=len(value)), self.assertRaises(ValueError):
                    next(agent.status_stream(path))

    @unittest.skipUnless(sys.platform == 'linux', 'inotify is a Linux interface')
    def test_atomic_snapshots_notify_battery_without_waiting_for_a_poll(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent, 'RUNTIME', Path(directory)):
            source, state = Path(directory) / 'status.json', Path(directory) / 'state'
            watch = feed.StatusWatch(source)
            try:
                for online in (True, False, True):
                    agent.save_status(status(online), 'virtio-serial', None)
                    self.assertTrue(watch.wait(timeout=0.5))
                    self.assertTrue(feed.publish(source, state))
                    self.assertEqual(state.read_text(), f'1 {int(online)} 42 {int(online)}\n')
                source.unlink()
                Path(directory).rename(Path(directory + '-moved'))
                with self.assertRaises(OSError):
                    watch.wait(timeout=0.5)
                Path(directory + '-moved').rename(directory)
            finally:
                watch.close()

    def test_stale_and_invalid_telemetry_do_not_refresh_the_watchdog(self):
        with tempfile.TemporaryDirectory() as directory:
            source, state = Path(directory) / 'status.json', Path(directory) / 'state'
            source.write_text(json.dumps(status()))
            os.utime(source, (1, 1))
            self.assertFalse(feed.publish(source, state))
            self.assertFalse(state.exists())
            for field, value in [('Max Capacity', 0), ('Current Capacity', True),
                                 ('Current Capacity', 200), ('Is Charging', 'yes')]:
                invalid = status(); invalid['battery'][field] = value
                source.write_text(json.dumps(invalid))
                self.assertFalse(feed.publish(source, state))
                self.assertFalse(state.exists())
