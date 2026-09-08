"""Fetch selected, pinned UTM release assets without installing or running UTM."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative_path(value):
    path = Path(value)
    if not value or path.is_absolute() or '..' in path.parts or path == Path('.'):
        raise ValueError('Expected a nonempty relative asset path')
    return path


def tree_digest(directory):
    """Hash names, contents, executable bits and internal links, not timestamps."""
    directory = directory.resolve(strict=True)
    entries = []
    for path in sorted(directory.rglob('*')):
        name = path.relative_to(directory).as_posix()
        info = path.lstat()
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or not path.resolve(strict=True).is_relative_to(directory):
                raise ValueError('Asset symlink escapes its component: ' + name)
            entries.append([name, 'link', target])
        elif stat.S_ISREG(info.st_mode):
            entries.append([name, 'file', bool(info.st_mode & 0o111), digest(path)])
        elif stat.S_ISDIR(info.st_mode):
            entries.append([name, 'directory'])
        else:
            raise ValueError('Unsupported asset entry: ' + name)
    return hashlib.sha256(json.dumps(entries, separators=(',', ':')).encode()).hexdigest()


class UTMAssets:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.lock = json.loads((self.root / 'upstream/utm-assets.lock.json').read_text())
        self.target = self.root / 'build/upstream/utm'

    def verify(self, directory):
        directory = directory.resolve(strict=True)
        for item in self.lock['components']:
            relative = relative_path(item['path'])
            path = directory / relative
            if (path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(directory)
                    or tree_digest(path) != item['treeSHA256']):
                raise RuntimeError('UTM asset does not match its pin: ' + str(relative))

    def verify_shader_sources(self, sources):
        for relative, expected in self.lock['shaderSources'].items():
            if digest(sources / relative_path(relative)) != expected:
                raise RuntimeError('CocoaSpice shader sources changed; update the pinned shader asset or use --compile-shaders')

    def download(self):
        archive = self.lock['archive']
        cache = self.root / 'build/downloads'
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / 'UTM.dmg'
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix='.utm-download-', dir=cache) as temp:
                part = Path(temp) / 'UTM.dmg'
                request = urllib.request.Request(archive['url'], headers={'User-Agent': 'Ashacky-build'})
                with urllib.request.urlopen(request, timeout=60) as response, part.open('wb') as output:
                    shutil.copyfileobj(response, output)
                if part.stat().st_size != archive['size'] or digest(part) != archive['sha256']:
                    raise RuntimeError('UTM download checksum/size mismatch')
                part.rename(target)
        if target.stat().st_size != archive['size'] or digest(target) != archive['sha256']:
            raise RuntimeError('Cached UTM.dmg differs from its pin; remove build/downloads/UTM.dmg and retry')
        return target

    def prepare(self):
        if platform.system() != 'Darwin':
            raise RuntimeError('UTM disk-image extraction runs on macOS with hdiutil')
        receipt = self.target / '.ashacky-assets.json'
        if self.target.exists():
            if not receipt.is_file() or json.loads(receipt.read_text()) != self.lock:
                raise RuntimeError('UTM asset inputs changed; remove build/upstream/utm and prepare again')
            self.verify(self.target)
            return self.target
        archive = self.download()
        self.target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.utm-extract-', dir=self.target.parent))
        mount = temporary / 'mount'
        mount.mkdir()
        attached = False
        try:
            subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount),
                            str(archive)], check=True, stdout=subprocess.DEVNULL)
            attached = True
            contents = mount / 'UTM.app/Contents'
            self.verify(contents)
            output = temporary / 'assets'
            output.mkdir()
            for item in self.lock['components']:
                relative = relative_path(item['path'])
                destination = output / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(contents / relative, destination, symlinks=True)
                # Disk-image directories can be read-only. Keep generated build
                # inputs editable/removable while preserving executable bits.
                for path in [destination, *destination.rglob('*')]:
                    if not path.is_symlink():
                        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
            self.verify(output)
            (output / '.ashacky-assets.json').write_text(json.dumps(self.lock, indent=2) + '\n')
            output.rename(self.target)
        finally:
            if attached:
                subprocess.run(['hdiutil', 'detach', str(mount)], check=True, stdout=subprocess.DEVNULL)
            shutil.rmtree(temporary)
        return self.target
