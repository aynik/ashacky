"""Volume behavior and desktop RPC boundary; no physical audio devices."""
import copy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'guest/devices'))
from audio_volume import OutputVolume
from audio_pipewire import AudioBridge
from device_service import validate


def device(level=.75, muted=False, uid='test'):
    return {'uid': uid, 'defaultOutput': True, 'outputVolume': {'level': level, 'muted': muted}}


def node(level=1, muted=False, soft=None, ident=10):
    return {'id': ident, 'info': {'params': {'Props': [{'channelVolumes': [level, level],
        'mute': muted, 'softVolumes': [level if soft is None else soft] * 2, 'softMute': muted}]}}}


class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.write = Mock()
        self.change = Mock()
        self.later = Mock()
        self.volume = OutputVolume(self.write, lambda: self.now)

    def sync(self, host, public):
        return self.volume.reconcile(host, public, self.change, self.later)

    def accept(self, level=.75, muted=False):
        self.sync(device(level, muted), node())
        self.sync(device(level, muted), node(level ** 3, muted, soft=1))
        self.write.reset_mock()

    def test_startup_adopts_host_without_squaring_gain_or_host_write(self):
        self.sync(device(.5), node())
        self.write.assert_called_once_with(10, (.125, .125), False, (1, 1))
        self.change.assert_not_called()

    def test_keyboard_updates_display_and_has_no_echo(self):
        self.accept()
        self.sync(device(.5), node(.75 ** 3, soft=1))
        self.write.assert_called_once_with(10, (.125, .125), False, (1, 1))
        self.sync(device(.5), node(.75 ** 3, soft=1)) # delayed pre-write frame
        self.sync(device(.5), node(.125, soft=1))
        self.change.assert_not_called()
        self.assertEqual(self.write.call_count, 1)

    def test_guest_slider_writes_host_and_restores_unity_gain(self):
        self.accept()
        self.change.return_value = {'devices': [device(.5)]}
        self.sync(device(), node(.125))
        self.change.assert_called_once_with({'action': 'audio-volume', 'uid': 'test',
            'desired': {'level': .125 ** (1 / 3), 'muted': False}, 'expected': {'level': .75, 'muted': False}})
        self.write.assert_called_once_with(10, (.125, .125), False, (1, 1))
        self.sync(device(.5), node(.125, soft=1))
        self.assertEqual(self.change.call_count, 1)

    def test_host_conflict_and_quantization_are_authoritative(self):
        self.accept()
        self.change.return_value = {'devices': [device(.625, True)]}
        self.sync(device(), node(.125))
        self.write.assert_called_once_with(10, (.625 ** 3,) * 2, True, (1, 1))

    def test_zero_and_mute_are_independent_and_balance_survives_zero(self):
        self.accept()
        self.change.return_value = {'devices': [device(0, False)]}
        self.sync(device(), node(0))
        self.write.assert_called_once_with(10, (0, 0), False, (1, 1))
        self.sync(device(0), node(0, soft=1))
        self.sync(device(.5, True), node(0, soft=1))
        self.write.assert_called_with(10, (.125, .125), True, (1, 1))

    def test_host_hotplug_does_not_apply_old_output_volume(self):
        self.accept()
        self.change.return_value = {'devices': [device(.2, uid='replacement')]}
        self.sync(device(), node(.125))
        self.write.assert_called_once_with(10, (.125, .125), False, (.125, .125))
        self.assertIsNone(self.volume.key)
        self.sync(device(.2, uid='replacement'), node(ident=20))
        self.write.assert_called_with(20, (.2 ** 3,) * 2, False, (1, 1))

    def test_unsupported_or_older_host_keeps_software_gain(self):
        self.sync({'uid': 'test'}, node(.125, True))
        self.write.assert_not_called()
        self.accept()
        self.sync({'uid': 'test'}, node(.125, True, soft=1))
        self.write.assert_called_once_with(10, (.125, .125), True, (.125, .125))
        self.change.assert_not_called()

    def test_failure_keeps_local_mute_and_can_recover(self):
        self.accept()
        public = node(.125, True)
        self.change.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError): self.sync(device(), public)
        self.volume.fallback(public)
        self.write.assert_called_once_with(10, (.125, .125), True, (.125, .125))
        self.change.side_effect = None
        self.sync(device(.5, True), public)
        self.write.assert_called_with(10, (.125, .125), True, (1, 1))

    def test_amplification_is_clamped_and_balance_retained(self):
        self.accept()
        self.change.return_value = {'devices': [device(1)]}
        public = node(8)
        public['info']['params']['Props'][0]['channelVolumes'][1] = 4
        self.sync(device(), public)
        self.assertEqual(self.change.call_args.args[0]['desired']['level'], 1)
        self.write.assert_called_once_with(10, (1, .5), False, (1, .5))

    def test_missing_echo_has_a_bounded_deadline(self):
        self.sync(device(), node())
        self.later.assert_called_once_with('volume-deadline', 2100)
        self.now = 3
        self.change.return_value = {'devices': [device(1)]}
        self.sync(device(), node())
        self.change.assert_not_called()
        self.assertEqual(self.write.call_count, 2)

    def test_management_failure_keeps_output_and_microphone_endpoints(self):
        bridge = AudioBridge.__new__(AudioBridge)
        public = node(.125, True)
        bridge.volume = Mock(key=('test', 10))
        bridge.children = {'output': Mock(), 'input': Mock()}
        bridge.stop_child = Mock()
        bridge.state = {'devices': []}
        bridge.events = SimpleNamespace(graph=SimpleNamespace(ready=True, nodes={10: public}),
            snapshot=Mock(side_effect=RuntimeError('management unavailable')), retry=Mock())
        bridge.reconcile()
        bridge.stop_child.assert_not_called()
        self.assertEqual(set(bridge.children), {'output', 'input'})
        bridge.volume.fallback.assert_called_once_with(public)
        bridge.events.retry.assert_called_once()

    def test_invalid_requests_never_pass_the_desktop_proxy(self):
        good = {'action': 'audio-volume', 'uid': 'test',
                'desired': {'level': .5, 'muted': False},
                'expected': {'level': .75, 'muted': True}}
        self.assertEqual(validate(good), good)
        for field in ('desired', 'expected'):
            for value in (True, '0.5', None, -1, 1.1, math.inf, math.nan):
                bad = copy.deepcopy(good); bad[field]['level'] = value
                with self.assertRaises(ValueError): validate(bad)
            for value in (0, 1, 'false', None):
                bad = copy.deepcopy(good); bad[field]['muted'] = value
                with self.assertRaises(ValueError): validate(bad)
            bad = copy.deepcopy(good); bad[field]['extra'] = 'input'
            with self.assertRaises(ValueError): validate(bad)
        for uid in ('', 'x' * 1025, None, 3):
            with self.assertRaises(ValueError): validate(good | {'uid': uid})
        with self.assertRaises(ValueError): validate(good | {'direction': 'input'})
