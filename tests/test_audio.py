import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'guest/devices'))
import audio_events as events
import audio_pipewire as audio


def node(ident=10, volume=0.5, mute=False):
    return {'id': ident, 'type': events.NODE, 'info': {
        'state': 'idle', 'props': {'node.name': 'linuxhost.output.test', 'media.class': 'Audio/Sink'},
        'params': {'Props': [{'channelVolumes': [volume, volume], 'mute': mute}]}}}


def metadata(values):
    return {'id': 4, 'type': events.METADATA, 'props': {'metadata.name': 'default'},
            'metadata': [{'subject': 0, 'key': k, 'value': {'name': v}} for k, v in values.items()]}


class AudioTests(unittest.TestCase):
    def test_fragmented_unicode_and_multiple_pipewire_frames(self):
        stream = events.JSONStream()
        data = (json.dumps([{'id': 1, 'name': '音声'}], ensure_ascii=False) + '\n[]\n').encode()
        result = []
        for byte in data:
            result.extend(stream.feed(bytes([byte])))
        self.assertEqual(result, [[{'id': 1, 'name': '音声'}], []])
        with self.assertRaises(ValueError): events.JSONStream(limit=10).feed(b'[' + b' ' * 10)
        with self.assertRaises(ValueError): events.JSONStream().feed(b'{}')

    def test_only_meaningful_node_changes_wake_audio(self):
        graph = events.Graph()
        self.assertTrue(graph.update([node()]))
        self.assertFalse(graph.update([{'id': 22, 'type': 'PipeWire:Interface:Client'}]))
        self.assertFalse(graph.update([{'id': 22, 'info': None}]))
        changed = node(); changed['info']['state'] = 'running'
        self.assertFalse(graph.update([changed]))
        self.assertTrue(graph.update([node(mute=True)]))
        self.assertTrue(graph.update([{'id': 10, 'info': None}]))
        self.assertEqual(graph.nodes, {})

    def test_metadata_snapshot_handles_deletions_without_polling(self):
        graph = events.Graph()
        key = 'default.configured.audio.sink'
        initial = metadata({key: 'one', 'unrelated': 'two'})
        graph.update([initial])
        query = lambda ident: [initial]
        self.assertEqual(audio.defaults(graph.snapshot(query))[key], 'one')
        self.assertFalse(graph.dirty_metadata)
        # An empty monitor metadata delta can mean a deleted key. A fresh
        # snapshot of this object removes it, without dumping the entire graph.
        graph.update([metadata({})])
        self.assertNotIn(key, audio.defaults(graph.snapshot(lambda ident: [metadata({'unrelated': 'two'})])))
        self.assertTrue(graph.update([{'id': 4, 'info': None}]))
        self.assertEqual(graph.metadata, {})

    def test_host_revision_expires_and_legacy_hosts_have_no_event_capability(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'status.json'
            self.assertIsNone(events.host_revision(path)[0])
            path.write_text(json.dumps({'ok': True, 'statusTransport': 'virtio-serial', 'audioRevision': 'epoch:1', 'active': True, 'wakeGeneration': 0}))
            self.assertEqual(events.host_revision(path)[0], ('epoch:1', True, 0))
            os.utime(path, (1, 1))
            self.assertIsNone(events.host_revision(path)[0])
            path.write_text(json.dumps({'ok': True, 'statusTransport': 'rpc-poll'}))
            self.assertIsNone(events.host_revision(path)[0])

    def test_public_mute_and_zero_volume_never_raise_transport(self):
        transport = node(11)
        transport['info']['props']['node.name'] = audio.TRANSPORT['output']
        for public in (node(mute=True), node(volume=0)):
            with patch.object(audio.subprocess, 'run') as run:
                audio.prepare_transport('linuxhost.output.test', 'output', [public, transport], {})
                run.assert_not_called()

    def test_inactive_host_waits_for_an_event_instead_of_repeated_rpcs(self):
        watcher = events.AudioEvents.__new__(events.AudioEvents)
        watcher.host_key = ('epoch:1', False, 0)
        watcher.later = Mock()
        watcher.retry()
        watcher.later.assert_not_called()
        watcher.host_key = ('epoch:1', True, 0)
        watcher.retry()
        watcher.later.assert_called_once()

    @unittest.skipUnless(sys.platform == 'linux', 'GLib/Linux integration')
    def test_event_subscriptions_and_child_cleanup_without_audio_routes(self):
        bridge = audio.AudioBridge()
        from gi.repository import GLib
        process = subprocess.Popen([sys.executable, '-c', 'pass'])
        bridge.children['test'] = process
        bridge.child_watches['test'] = GLib.child_watch_add(GLib.PRIORITY_DEFAULT, process.pid,
            bridge.child_exited, 'test', process)
        loop = GLib.MainLoop()
        limit = GLib.timeout_add(200, lambda: (loop.quit(), False)[1])
        loop.run()
        self.assertNotIn('test', bridge.children)
        self.assertEqual(process.returncode, 0)
        bridge.events.close()

    @unittest.skipUnless(sys.platform == 'linux', 'GLib/Linux integration')
    def test_pipewire_stream_disconnect_stops_for_service_recovery(self):
        # A temporary child emits a realistic initial snapshot then exits.
        # No connection to the real PipeWire server or host is made.
        original_popen = subprocess.Popen
        def fake_popen(*args, **kwargs):
            return original_popen([sys.executable, '-c', 'print("[]", flush=True)'], stdout=subprocess.PIPE)
        seen = []
        watcher = events.AudioEvents(lambda: seen.append(True))
        with patch.object(events.subprocess, 'Popen', fake_popen), patch.object(watcher, 'watch_status'):
            try:
                with self.assertRaisesRegex(RuntimeError, 'disconnected'):
                    watcher.run(2)
                self.assertTrue(watcher.graph.ready)
            finally:
                watcher.close()
