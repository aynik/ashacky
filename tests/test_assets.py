import importlib.util
from pathlib import Path
import struct
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('builder', ROOT / 'tools/ashacky.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class IconTests(unittest.TestCase):
    def test_source_icon_passes_binary_validation(self):
        builder.check_icon((ROOT / 'host/frontend/assets/Ashacky.png').read_bytes())

    def test_metadata_and_damaged_images_are_rejected(self):
        icon = (ROOT / 'host/frontend/assets/Ashacky.png').read_bytes()
        payload = b'tEXtDescription\0metadata'
        chunk = struct.pack('>I', len(payload) - 4) + payload + struct.pack('>I', zlib.crc32(payload))
        for data in (icon[:-12] + chunk + icon[-12:], icon[:-1], b'not an image'):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                builder.check_icon(data)
