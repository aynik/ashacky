"""Dependency preparation must preserve pins and never edit a submodule."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ashacky_sources', ROOT / 'tools/sources.py')
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.upstream = base / 'upstream'
        self.root = base / 'checkout'
        self.git('init', '-q', str(self.upstream))
        (self.upstream / 'value.txt').write_text('upstream\n')
        self.git('-C', str(self.upstream), 'add', '.')
        self.git('-C', str(self.upstream), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-qm', 'fixture')
        self.git('init', '-q', str(self.root))
        self.git('-C', str(self.root), '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q',
                 str(self.upstream), 'third_party/demo')
        self.module = self.root / 'third_party/demo'
        (self.root / 'patches').mkdir()
        self.patch = self.root / 'patches/demo.patch'
        self.patch.write_text('--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-upstream\n+ashacky\n')
        (self.root / 'upstream').mkdir()
        (self.root / 'upstream/archives.lock.json').write_text('{"archives": {}}')
        (self.root / 'dependencies.json').write_text(json.dumps({'sources': {
            'demo': {'submodule': 'third_party/demo', 'patches': ['patches/demo.patch']},
            'plain': {'submodule': 'third_party/demo'},
        }}))
        self.sources = sources.Sources(self.root)

    def git(self, *args):
        return subprocess.check_output(['git', *args], stderr=subprocess.PIPE, text=True).strip()

    def test_patch_preserves_pristine_submodule_and_reuses_build_tree(self):
        prepared = self.sources.prepare('demo')
        self.assertEqual((prepared / 'value.txt').read_text(), 'ashacky\n')
        self.assertEqual((self.module / 'value.txt').read_text(), 'upstream\n')
        self.assertFalse(self.git('-C', str(self.module), 'status', '--porcelain'))
        self.assertEqual(self.sources.prepare('demo'), prepared)
        self.assertEqual(self.sources.prepare('plain'), self.module)

    def test_cached_export_does_not_bypass_dirty_submodule_check(self):
        prepared = self.sources.prepare('demo')
        (self.module / 'value.txt').write_text('local changes\n')
        with self.assertRaisesRegex(RuntimeError, 'local changes'):
            self.sources.prepare('demo')
        self.assertEqual((prepared / 'value.txt').read_text(), 'ashacky\n')

    def test_nested_submodule_is_exported_and_verified(self):
        self.git('-C', str(self.module), '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q',
                 str(self.upstream), 'nested/demo')
        self.git('-C', str(self.module), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-qm', 'nested dependency')
        self.git('-C', str(self.module), 'push', '-q', 'origin', 'HEAD:refs/heads/nested-fixture')
        self.git('-C', str(self.root), 'add', '.')
        self.git('-C', str(self.root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-qm', 'source recipes and nested pins')
        clone = Path(self.temp.name) / 'fresh-clone'
        self.git('clone', '-q', str(self.root), str(clone))
        self.git('-C', str(clone), '-c', 'protocol.file.allow=always',
                 'submodule', 'update', '--init', '--recursive', '--depth', '1')
        fresh_sources = sources.Sources(clone)
        prepared = fresh_sources.prepare('demo')
        self.assertEqual((prepared / 'nested/demo/value.txt').read_text(), 'upstream\n')
        self.assertFalse((prepared / 'nested/demo/.git').exists())
        self.git('-C', str(clone / 'third_party/demo'), 'submodule', 'deinit', '-f', '--', 'nested/demo')
        with self.assertRaisesRegex(RuntimeError, 'git submodule update --init'):
            fresh_sources.prepare('demo')

    def test_unrecorded_dependency_commit_is_rejected(self):
        (self.module / 'new.txt').write_text('new\n')
        self.git('-C', str(self.module), 'add', '.')
        self.git('-C', str(self.module), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-qm', 'unrecorded dependency update')
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            self.sources.prepare('demo')

    def test_changed_patch_requires_explicit_generated_tree_rebuild(self):
        self.sources.prepare('demo')
        self.patch.write_text(self.patch.read_text().replace('+ashacky', '+updated'))
        with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
            self.sources.prepare('demo')

    def test_uninitialized_submodule_has_native_git_recovery(self):
        self.git('-C', str(self.root), 'submodule', 'deinit', '-f', '--', 'third_party/demo')
        with self.assertRaisesRegex(RuntimeError, 'git submodule update --init'):
            self.sources.prepare('demo')

    def test_archive_cannot_escape_extraction_directory(self):
        archive = Path(self.temp.name) / 'malicious.tar'
        with tarfile.open(archive, 'w') as bundle:
            item = tarfile.TarInfo('../escaped.txt')
            item.size = 1
            bundle.addfile(item, io.BytesIO(b'x'))
        destination = Path(self.temp.name) / 'extract'
        destination.mkdir()
        with self.assertRaises(tarfile.FilterError):
            sources.Sources.extract(archive, destination)
        self.assertFalse((destination.parent / 'escaped.txt').exists())
