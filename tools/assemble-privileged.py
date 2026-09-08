#!/usr/bin/env python3
"""Stage root-helper binaries and their libraries in build/, without installing."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
spec = importlib.util.spec_from_file_location('assembly', ROOT / 'tools/assemble-runtime.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if platform.system() != 'Darwin':
        raise RuntimeError('Stage privileged helpers on macOS')
    output = BUILD / 'host/PrivilegedHelpers'
    if output.is_symlink():
        raise RuntimeError('Refusing a redirected output')
    if output.exists() and subprocess.run(['/usr/sbin/lsof', '-t', '+D', str(output)],
                                         capture_output=True, text=True).stdout.strip():
        raise RuntimeError('Helper outputs are in use')
    brew = Path(a.run('brew', '--prefix').strip()).resolve()
    with tempfile.TemporaryDirectory(prefix='root-helper-stage-', dir=BUILD / 'runtime') as temporary:
        stage = Path(temporary) / 'PrivilegedHelpers'
        libraries = stage / 'lib'
        libraries.mkdir(parents=True)
        origins, copied, queue = {}, {}, []

        def copy(source, target):
            source = source.resolve(strict=True)
            if source in copied:
                return copied[source]
            if target.exists():
                raise RuntimeError('Helper library collision: ' + str(target))
            shutil.copy2(source, target)
            copied[source] = target
            origins[target] = source
            queue.append(target)
            return target

        for name in ('LinuxHostPower', 'linuxhost-usb-host', 'socket_vmnet', 'wifi-interface'):
            copy(BUILD / 'host' / name, stage / name)
        manifest = {}
        for path in queue:
            source = origins[path]
            deps, rpaths, ids = a.commands(path)
            changes = []
            for dep in deps:
                if a.system(dep):
                    continue
                if dep.startswith('@rpath/'):
                    candidates = [Path(root.replace('@loader_path', str(source.parent))) / dep[7:] for root in rpaths]
                else:
                    candidates = [Path(dep.replace('@loader_path', str(source.parent)))]
                resolved = next((p.resolve() for p in candidates if p.is_file()), None)
                if resolved is None or not resolved.is_relative_to(brew):
                    raise RuntimeError('Unexpected root-helper dependency: ' + dep)
                target = copy(resolved, libraries / resolved.name)
                changes += ['-change', dep, '@loader_path/' + os.path.relpath(target, path.parent)]
            if ids:
                changes += ['-id', '@loader_path/' + path.name]
            for root in rpaths:
                if not a.system(root):
                    changes += ['-delete_rpath', root]
            if changes:
                a.run('install_name_tool', *changes, path)
            a.run('codesign', '--force', '--sign', '-', path)
            a.run('codesign', '--verify', '--strict', path)
            for dep in a.commands(path)[0]:
                if not a.system(dep):
                    if not dep.startswith('@loader_path/') or not (path.parent / dep[13:]).resolve(strict=True).is_relative_to(stage):
                        raise RuntimeError('Unbundled helper dependency: ' + dep)
            manifest[str(path.relative_to(stage))] = str(source)
        (stage / 'dependencies.json').write_text(json.dumps(manifest, indent=2) + '\n')
        if output.exists():
            shutil.rmtree(output)
        stage.rename(output)
        print(f'Staged {len(queue)} signed helper binaries/libraries: {output}. Nothing installed or started.')


if __name__ == '__main__':
    main()
