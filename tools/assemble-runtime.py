#!/usr/bin/env python3
"""Assemble and ad-hoc sign the local runtime. Does not install or launch it."""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import tempfile

from utm_assets import UTMAssets

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
MAGIC = {bytes.fromhex(value) for value in ('feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}


def run(*args):
    return subprocess.check_output(list(map(str, args)), text=True)


def macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open('rb') as stream:
        return stream.read(4) in MAGIC


def system(path):
    return path.startswith(('/System/', '/usr/lib/'))


def commands(path):
    text = run('otool', '-arch', 'arm64', '-l', path)
    rpaths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset', text)
    ids = re.findall(r'cmd LC_ID_DYLIB\s+cmdsize \d+\s+name (.*?) \(offset', text)
    deps = [line.strip().split(' (compatibility')[0] for line in run('otool', '-arch', 'arm64', '-L', path).splitlines()[1:]]
    return [name for name in deps if name not in ids], rpaths, ids


def inspect_bundle(app):
    contents = app / 'Contents'
    count = 0
    for path in contents.rglob('*'):
        if path.is_symlink() and not path.resolve(strict=True).is_relative_to(contents.resolve()):
            raise RuntimeError('Bundle symlink escapes: ' + str(path))
        if not macho(path):
            continue
        deps, rpaths, _ = commands(path)
        for name in deps:
            if system(name):
                continue
            if not name.startswith('@loader_path/'):
                raise RuntimeError('Nonlocal dependency: ' + str(path) + ': ' + name)
            resolved = (path.parent / name[len('@loader_path/'):]).resolve(strict=True)
            if not resolved.is_relative_to(contents.resolve()):
                raise RuntimeError('Dependency escapes bundle: ' + str(path))
        if any(not system(value) for value in rpaths):
            raise RuntimeError('Obsolete build rpath: ' + str(path))
        count += 1
    return count


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Assemble on Apple Silicon macOS')
    app = BUILD / 'host/Ashacky.app'
    opened = subprocess.run(['/usr/sbin/lsof', '-t', '+D', str(app)], capture_output=True, text=True)
    if opened.stdout.strip() or app.is_symlink():
        raise RuntimeError('Refusing to replace a running or redirected build bundle')
    assets = UTMAssets().prepare()
    brew = Path(run('brew', '--prefix').strip()).resolve()
    prefix = BUILD / 'runtime/prefix'
    # The allowlist prevents accidentally borrowing from the reference runtime.
    allowed = [BUILD.resolve(), brew]
    search = [assets / 'Frameworks', prefix / 'lib', BUILD / 'runtime/virglrenderer/src', brew / 'lib']
    with tempfile.TemporaryDirectory(prefix='assemble-', dir=BUILD / 'runtime') as temporary:
        staged = Path(temporary) / 'Ashacky.app'
        shutil.copytree(app, staged, symlinks=True)
        contents = staged / 'Contents'
        frameworks = contents / 'Frameworks'
        shutil.rmtree(frameworks, ignore_errors=True)
        shutil.copytree(assets / 'Frameworks', frameworks, symlinks=True)
        resources = contents / 'Resources'
        origins = {}
        for path in contents.rglob('*'):
            if macho(path):
                relative = path.relative_to(contents)
                origins[path] = ((assets / relative) if relative.parts[0] == 'Frameworks'
                                 else app / 'Contents' / relative).resolve()
        for name in ('qemu-system-aarch64', 'qemu-img', 'virgl_render_server', 'LinuxHostControl',
                     'LinuxHostVideoShared', 'vtremoted', 'SessionSync'):
            target = contents / 'MacOS' / name
            source = BUILD / 'host' / name
            shutil.copy2(source, target)
            origins[target] = source.resolve()
        for name in ('gst-inspect-1.0', 'gst-launch-1.0'):
            target = contents / 'MacOS' / name
            shutil.copy2(prefix / 'bin' / name, target)
            origins[target] = (prefix / 'bin' / name).resolve()
        for name in ('edk2-aarch64-code.fd', 'edk2-licenses.txt'):
            shutil.copy2(BUILD / 'host' / name, resources / name)
        plugins = resources / 'gstreamer-1.0'
        shutil.rmtree(plugins, ignore_errors=True)
        shutil.copytree(prefix / 'lib/gstreamer-1.0', plugins, symlinks=True)
        for path in plugins.rglob('*'):
            if macho(path):
                origins[path] = (prefix / 'lib/gstreamer-1.0' / path.relative_to(plugins)).resolve()
        # Session's -L points here; ROMs and mutable firmware variables are not
        # copied from a user's VM. The display ROM used by EDK2 is included.
        qemu = resources / 'qemu'
        qemu.mkdir(exist_ok=True)
        for path in (BUILD / 'sources/qemu/pc-bios').glob('*'):
            if path.is_file() and (path.suffix in ('.bin', '.rom', '.dtb') or path.name.endswith('licenses.txt')):
                shutil.copy2(path, qemu / path.name)
        queue = list(origins)
        copied = {value: key for key, value in origins.items()}

        def copy_library(source):
            source = source.resolve(strict=True)
            if not any(source.is_relative_to(root) for root in allowed):
                raise RuntimeError('Dependency outside declared build inputs: ' + str(source))
            if source in copied:
                return copied[source]
            # Preserve a framework's structure rather than flattening its binary.
            relative = source.relative_to(assets.resolve()) if source.is_relative_to(assets.resolve()) else None
            target = contents / relative if relative else frameworks / source.name
            if target.exists():
                raise RuntimeError('Dependency name collision: ' + str(target))
            shutil.copy2(source, target)
            origins[target] = source
            copied[source] = target
            queue.append(target)
            return target

        # dlopen dependencies cannot be discovered with otool. The patched Swift
        # codec loads these stable names relative to its executable first.
        for formula, alias in [('lz4', 'liblz4.1.dylib'), ('zstd', 'libzstd.1.dylib')]:
            target = copy_library(brew / 'opt' / formula / 'lib' / alias)
            link = frameworks / alias
            if link != target:
                link.symlink_to(target.name)

        def expand(value, source):
            return Path(value.replace('@loader_path', str(source.parent)).replace('@executable_path', str(app / 'Contents/MacOS')))

        def resolve(name, source, rpaths):
            if name.startswith('@rpath/'):
                tail = name[len('@rpath/'):]
                candidates = [expand(root, source) / tail for root in rpaths] + [root / tail for root in search]
            else:
                candidates = [expand(name, source)]
            for candidate in candidates:
                if candidate.is_file():
                    return candidate.resolve()
            raise RuntimeError('Unresolved dependency: ' + str(source) + ': ' + name)

        manifest = {}
        for path in queue:
            source = origins[path]
            archs = run('lipo', '-archs', path).split()
            if 'arm64' not in archs:
                raise RuntimeError('Dependency has no arm64 architecture: ' + str(source))
            if len(archs) > 1:
                thin = path.with_name(path.name + '.thin')
                run('lipo', path, '-thin', 'arm64', '-output', thin)
                thin.replace(path)
            deps, rpaths, ids = commands(path)
            changes = []
            edges = []
            for name in deps:
                if system(name):
                    continue
                target = copy_library(resolve(name, source, rpaths))
                changes += ['-change', name, '@loader_path/' + os.path.relpath(target, path.parent)]
                edges.append(str(target.relative_to(contents)))
            if ids:
                changes += ['-id', '@loader_path/' + path.name]
            for value in rpaths:
                if not system(value):
                    changes += ['-delete_rpath', value]
            if changes:
                run('install_name_tool', *changes, path)
            manifest[str(path.relative_to(contents))] = {'source': str(source), 'dependencies': edges}
        (resources / 'MoltenVK_icd.json').write_text(json.dumps({'file_format_version': '1.0.0',
            'ICD': {'library_path': '../Frameworks/MoltenVK.framework/MoltenVK',
                    'api_version': '1.2.0', 'is_portability_driver': True}}, indent=2) + '\n')
        # These are private build records, never tracked source or a VM image.
        (resources / 'dependencies.json').write_text(json.dumps(manifest, indent=2) + '\n')
        notices = resources / 'Notices'
        shutil.rmtree(notices, ignore_errors=True)
        notices.mkdir()
        for name in ('LICENSE', 'THIRD_PARTY.md'):
            shutil.copy2(ROOT / name, notices / name)
        cellar = brew / 'Cellar'
        formula_roots = set()
        for source in origins.values():
            if source.is_relative_to(cellar):
                relative = source.relative_to(cellar)
                formula_roots.add(cellar / relative.parts[0] / relative.parts[1])
        for source in [*ROOT.glob('third_party/*'), *sorted(formula_roots)]:
            files = [p for p in source.iterdir() if p.is_file() and p.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE'))]
            if files:
                destination = notices / ('brew-' + source.parent.name if source.is_relative_to(brew) else source.name)
                destination.mkdir(exist_ok=True)
                for path in files:
                    shutil.copy2(path, destination / path.name)
        minimum = (13, 0)
        for path in queue:
            for block in run('otool', '-arch', 'arm64', '-l', path).split('Load command '):
                pattern = r'\bminos ([\d.]+)' if 'cmd LC_BUILD_VERSION\n' in block else r'\bversion ([\d.]+)'
                if 'cmd LC_BUILD_VERSION\n' in block or 'cmd LC_VERSION_MIN_MACOSX\n' in block:
                    match = re.search(pattern, block)
                    if match:
                        minimum = max(minimum, tuple(map(int, match[1].split('.'))))
        info = plistlib.loads((contents / 'Info.plist').read_bytes())
        info['LSMinimumSystemVersion'] = '.'.join(map(str, minimum))
        (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
        for path in queue:
            extra = ['--entitlements', ROOT / 'host/frontend/hypervisor-entitlements.plist'] if path.name == 'qemu-system-aarch64' else []
            run('codesign', '--force', '--sign', '-', *extra, path)
        nested = [path for path in contents.rglob('*') if path.is_dir() and path.suffix in ('.framework', '.bundle')]
        for path in sorted(nested, key=lambda p: len(p.parts), reverse=True):
            run('codesign', '--force', '--sign', '-', path)
        count = inspect_bundle(staged)
        run('codesign', '--force', '--sign', '-', '--entitlements',
            ROOT / 'host/frontend/app-entitlements.plist', staged)
        run('codesign', '--verify', '--deep', '--strict', staged)
        shutil.rmtree(app)
        staged.rename(app)
        print(f'Assembled {count} Mach-O files with local dependency paths; signatures verified: {app}')


if __name__ == '__main__':
    main()
