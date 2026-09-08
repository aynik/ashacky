import sys
import platform
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'guest/input'))
if platform.system() == 'Linux':
    import trackpad

class Recorder:
    def __init__(self): self.events = []
    def write(self, *args): self.events.append(args)
    def syn(self): self.events.append(('syn',))

@unittest.skipUnless(platform.system() == 'Linux', 'Linux evdev tests run in the guest')
class TrackpadTests(unittest.TestCase):
    def test_contacts_are_released_on_empty_frame(self):
        recorder = Recorder()
        pad = trackpad.Pad(recorder)
        pad.frame(*trackpad.validate({'touches': [[1, .2, .3], [2, .6, .7]], 'buttons': 0}))
        self.assertEqual(len(pad.slots), 2)
        pad.frame(*trackpad.validate({'touches': [[2, .5, .6]], 'buttons': 1}))
        self.assertEqual(set(pad.slots), {2})
        pad.frame([], 0)
        self.assertFalse(pad.slots)
        self.assertEqual(pad.buttons, 0)
        self.assertIn((trackpad.e.EV_ABS, trackpad.e.ABS_MT_TRACKING_ID, -1), recorder.events)

    def test_invalid_coordinates_ids_and_buttons(self):
        for frame in [{'touches': [[1, 2, 0]]}, {'touches': [[1, 0, 0], [1, 0, 0]]}, {'touches': [], 'buttons': 8}]:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                trackpad.validate(frame)
