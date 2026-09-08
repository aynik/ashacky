"""Pinned binary inputs must fail closed and release mounted images on failure."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('utm_assets', ROOT / 'tools/utm_assets.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class UTMAssetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.contents = self.root / 'fixture'
        self.component = self.contents / 'Frameworks/Example.framework'
        real = self.component / 'Versions/A'
        real.mkdir(parents=True)
        self.binary = real / 'Example'
        self.binary.write_bytes(b'pinned binary')
        self.binary.chmod(0o755)
        (self.component / 'Versions/Current').symlink_to('A')
        (self.component / 'Example').symlink_to('Versions/Current/Example')
        self.lock = {'archive': {'url': 'https://example.invalid/UTM.dmg', 'size': 1, 'sha256': '0' * 64},
                     'components': [{'path': 'Frameworks/Example.framework',
                                     'treeSHA256': module.tree_digest(self.component)}],
                     'shaderSources': {'shader.metal': '0' * 64}}
        (self.root / 'upstream').mkdir()
        (self.root / 'upstream/utm-assets.lock.json').write_text(json.dumps(self.lock))
        self.assets = module.UTMAssets(self.root)

    def test_verification_accepts_internal_framework_links_and_detects_tampering(self):
        self.assets.verify(self.contents)
        self.binary.write_bytes(b'changed binary')
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            self.assets.verify(self.contents)

    def test_external_symlink_is_rejected(self):
        outside = self.root / 'outside'
        outside.write_text('not part of component')
        (self.component / 'external').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            module.tree_digest(self.component)

    def test_component_parent_cannot_redirect_outside_asset_root(self):
        external = self.root / 'external-frameworks'
        (self.contents / 'Frameworks').rename(external)
        (self.contents / 'Frameworks').symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            self.assets.verify(self.contents)

    def test_cached_receipt_does_not_hide_changed_assets(self):
        self.assets.target.parent.mkdir(parents=True)
        self.contents.rename(self.assets.target)
        (self.assets.target / '.ashacky-assets.json').write_text(json.dumps(self.lock))
        (self.assets.target / 'Frameworks/Example.framework/Versions/A/Example').write_bytes(b'changed')
        with patch.object(module.platform, 'system', return_value='Darwin'), \
                self.assertRaisesRegex(RuntimeError, 'does not match'):
            self.assets.prepare()

    def test_wrong_cached_download_is_rejected_before_mounting(self):
        cache = self.root / 'build/downloads'
        cache.mkdir(parents=True)
        (cache / 'UTM.dmg').write_bytes(b'wrong image')
        with self.assertRaisesRegex(RuntimeError, 'differs from its pin'):
            self.assets.download()

    def test_changed_shader_source_cannot_use_precompiled_shader(self):
        (self.root / 'shader.metal').write_text('different interface')
        with self.assertRaisesRegex(RuntimeError, 'shader sources changed'):
            self.assets.verify_shader_sources(self.root)

    def test_validation_failure_still_unmounts_image(self):
        with patch.object(module.platform, 'system', return_value='Darwin'), \
                patch.object(self.assets, 'download', return_value=self.root / 'verified.dmg'), \
                patch.object(self.assets, 'verify', side_effect=RuntimeError('bad contents')), \
                patch.object(module.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'bad contents'):
                self.assets.prepare()
        self.assertIn('-readonly', run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][:2], ['hdiutil', 'detach'])
        self.assertFalse(self.assets.target.exists())
