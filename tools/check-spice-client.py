#!/usr/bin/env python3
"""Check the bundled display acknowledgement against socket backpressure.

Compiles an isolated fixture against matching SPICE private headers/configuration,
then links it to the assembled app's libraries. No VM or service is opened.
"""
import argparse
import json
from pathlib import Path
import platform
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / 'build/host/Ashacky.app')
    parser.add_argument('--spice-build', type=Path, default=ROOT / 'build/runtime/spice-gtk',
                        help='Matching configured Meson build for the app\'s SPICE client')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        raise RuntimeError('Run the bundled SPICE check on macOS')
    build = args.spice_build.resolve()
    entries = json.loads((build / 'compile_commands.json').read_text())
    entry = next(item for item in entries if item['file'].endswith('/channel-display.c'))
    # Reuse only header paths and configuration macros, not arbitrary commands
    # or object/output flags from the compilation database.
    flags = [arg for arg in shlex.split(entry['command']) if arg.startswith(('-I', '-D'))]
    frameworks = args.app.resolve() / 'Contents/Frameworks'
    libraries = [frameworks / name for name in ('libspice-client-glib-2.0.8.dylib',
        'libgio-2.0.0.dylib', 'libgobject-2.0.0.dylib', 'libglib-2.0.0.dylib')]
    if not all(path.is_file() for path in libraries):
        raise RuntimeError('Assemble the candidate app before checking its SPICE client')
    with tempfile.TemporaryDirectory(prefix='spice-ack-check-', dir=ROOT / 'build/runtime') as directory:
        executable = Path(directory) / 'check-gl-ack'
        subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror',
            '-Wno-deprecated-declarations', *flags, str(ROOT / 'tests/spice-gl-ack.c'),
            *map(str, libraries), '-Wl,-headerpad_max_install_names', '-o', str(executable)],
            cwd=entry['directory'], check=True)
        # Bundled libraries have loader-relative IDs. The fixture lives outside
        # that directory, so bind only its own load commands to this candidate.
        changes = []
        for library in libraries:
            changes += ['-change', '@loader_path/' + library.name, str(library)]
        subprocess.run(['install_name_tool', *changes, str(executable)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(executable)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for mode in ('free', 'blocked'):
            result = subprocess.run([str(executable), mode], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                raise RuntimeError(f'{mode} socket failed: {result.stdout}{result.stderr}')
            print(f'Display acknowledgement queued without blocking ({mode} socket)')


if __name__ == '__main__':
    main()
