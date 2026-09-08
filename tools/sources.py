"""Prepare build inputs from clean, pinned submodules and verified release assets."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Sources:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.recipes = json.loads((self.root / 'dependencies.json').read_text())['sources']
        self.archives = json.loads((self.root / 'upstream/archives.lock.json').read_text())['archives']

    def path(self, relative):
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Build inputs must use repository-relative paths')
        return self.root / path

    @staticmethod
    def gitlinks(repository):
        result = {}
        entries = subprocess.check_output(['git', '-C', str(repository), 'ls-files', '--stage', '-z']).decode().split('\0')
        for entry in filter(None, entries):
            metadata, relative = entry.split('\t', 1)
            mode, revision, stage = metadata.split()
            if mode == '160000':
                if stage != '0':
                    raise RuntimeError(f'{relative}: unresolved submodule merge')
                result[relative] = revision
        return result

    def verify_checkout(self, path, revision):
        relative = path.relative_to(self.root)
        if not (path / '.git').exists():
            raise RuntimeError(f'{relative}: initialize with git submodule update --init --recursive --depth 1')
        actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != revision:
            raise RuntimeError(f'{relative}: checkout does not match the recorded submodule pin')
        dirty = subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=none'], text=True)
        if dirty:
            raise RuntimeError(f'{relative}: local changes found; move intended changes into Ashacky patches before building')
        for child, pin in self.gitlinks(path).items():
            self.verify_checkout(self.path(str(relative / child)), pin)

    def submodule(self, relative):
        path = self.path(relative)
        revision = self.gitlinks(self.root).get(relative)
        if not revision:
            raise RuntimeError(f'{relative}: expected one recorded submodule commit in the Git index')
        self.verify_checkout(path, revision)
        return path, revision

    def export(self, module, revision, destination, temporary):
        archive = temporary / 'export.tar'
        with archive.open('wb') as stream:
            subprocess.run(['git', '-C', str(module), 'archive', '--format=tar', revision], stdout=stream, check=True)
        self.extract(archive, destination)
        archive.unlink()
        # git archive does not include gitlink contents; export each validated
        # nested pin explicitly, preserving upstream's directory layout.
        for relative, pin in self.gitlinks(module).items():
            child = destination / relative
            child.mkdir(parents=True, exist_ok=True)
            self.export(module / relative, pin, child, temporary)

    def check_submodules(self):
        for recipe in self.recipes.values():
            if 'submodule' in recipe:
                self.submodule(recipe['submodule'])

    def inputs(self, recipe):
        pins = {}
        if 'submodule' in recipe:
            _, pins[recipe['submodule']] = self.submodule(recipe['submodule'])
        paths = [*recipe.get('patches', []), *recipe.get('copies', {})]
        for relative in paths:
            parts = Path(relative).parts
            if parts and parts[0] == 'third_party':
                module = str(Path(*parts[:2]))
                if module not in pins:
                    _, pins[module] = self.submodule(module)
        return {'recipe': recipe, 'submodules': pins,
                'files': {relative: digest(self.path(relative)) for relative in paths}}

    @staticmethod
    def extract(archive, destination, omitted=None):
        omitted = omitted or {}
        def safe_filter(member, target):
            # This exact EDK2 emulator link is unused by the ARM VM firmware.
            # Never relax extraction for arbitrary absolute/outside links.
            if member.name in omitted:
                if not member.issym() or member.linkname != omitted[member.name]:
                    raise RuntimeError('Unexpected external-link metadata')
                return None
            return tarfile.data_filter(member, target)
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter=safe_filter)

    def prepare(self, name):
        recipe = self.recipes[name]
        if '/' in name or name in ('.', '..'):
            raise ValueError('Invalid component name')
        identity = self.inputs(recipe)
        if 'archive' not in recipe and not recipe.get('patches') and not recipe.get('copies'):
            return self.path(recipe['submodule'])
        if 'archive' in recipe:
            identity['archive'] = self.archives[recipe['archive']]
        parent = self.root / 'build/sources'
        target = parent / name
        receipt = target / '.ashacky-source.json'
        if target.exists():
            if not receipt.is_file() or json.loads(receipt.read_text()) != identity:
                raise RuntimeError(f'{target}: inputs changed; remove this generated source directory and prepare again')
            return target
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.prepare-', dir=parent) as temp:
            temporary = Path(temp)
            source = temporary / 'source'
            source.mkdir()
            if 'archive' in recipe:
                spec = identity['archive']
                cache = self.root / 'build/downloads'
                cache.mkdir(parents=True, exist_ok=True)
                archive = cache / (recipe['archive'] + '.tar')
                if not archive.exists():
                    download = temporary / 'download'
                    urllib.request.urlretrieve(spec['url'], download)
                    if digest(download) != spec['sha256']:
                        raise RuntimeError('Downloaded archive checksum mismatch: ' + name)
                    download.rename(archive)
                if digest(archive) != spec['sha256']:
                    raise RuntimeError('Cached archive checksum mismatch: ' + name)
                extracted = temporary / 'extracted'
                extracted.mkdir()
                self.extract(archive, extracted, spec.get('omittedExternalLinks'))
                children = list(extracted.iterdir())
                if len(children) != 1 or not children[0].is_dir():
                    raise RuntimeError('Expected one archive source directory')
                source.rmdir()
                children[0].rename(source)
            else:
                module = recipe['submodule']
                # Export only a dependency requiring patches. Unmodified dependencies
                # build directly from their pristine submodule, without another copy.
                self.export(self.path(module), identity['submodules'][module], source, temporary)
            for relative in recipe.get('patches', []):
                result = subprocess.run(['patch', '--batch', '--forward', '-p1', '-i', str(self.path(relative))], cwd=source, capture_output=True, text=True)
                if result.returncode:
                    raise RuntimeError(f'{name}: patch failed: {relative}\n{result.stdout}\n{result.stderr}')
            for original, destination in recipe.get('copies', {}).items():
                if Path(destination).is_absolute() or '..' in Path(destination).parts:
                    raise ValueError('Invalid generated source destination')
                path = source / destination
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.path(original), path)
            (source / '.ashacky-source.json').write_text(json.dumps(identity, indent=2) + '\n')
            source.rename(target)
        return target

    def keymap(self):
        source = self.prepare('keycodemapdb')
        output = self.root / 'build/host/frontend-generated/keymap.h'
        output.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.check_output([sys.executable, str(source / 'tools/keymap-gen'),
            'code-map', '--lang=stdc', '--varname=mac_to_xt', 'data/keymaps.csv', 'osx', 'atset1'], cwd=source)
        output.write_bytes(result)
        return output
