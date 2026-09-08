#!/usr/bin/env python3
"""Repository checks and local builds. This command never installs services."""
import argparse
import ast
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'


def run(command, **kwargs):
    subprocess.run([str(value) for value in command], check=True, **kwargs)


def pkg(*packages):
    return shlex.split(subprocess.check_output(
        ['pkg-config', '--cflags', '--libs', *packages], text=True))


def source_files():
    for directory, children, files in os.walk(ROOT):
        children[:] = [name for name in children if name not in {'.git', 'build', 'third_party', '.local', '__pycache__', '.venv'}]
        for name in files:
            yield Path(directory) / name


def check():
    from sources import Sources
    Sources().check_submodules()
    failures = []
    count = 0
    # Deliberately report filenames/rule names, never possible secret values.
    private_rules = {
        'personal home path': r'/(?:Users|home)/(?!example\b)[A-Za-z0-9_-]+/',
        'private key': r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----',
        'GitHub credential': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
        'local mDNS hostname': r'(?i)\b[a-z0-9][a-z0-9-]*\.local\b',
    }
    forbidden_suffixes = {'.qcow2', '.img', '.iso', '.nvram', '.pem', '.key', '.log', '.so', '.dylib', '.ko'}
    for path in source_files():
        relative = str(path.relative_to(ROOT))
        count += 1
        if path.suffix in forbidden_suffixes:
            failures.append(relative + ': private/generated artifact')
            continue
        try:
            content = path.read_text()
            if path.suffix == '.py' or content.startswith('#!/usr/bin/python3') or content.startswith('#!/usr/bin/env python3'):
                ast.parse(content, filename=relative)
            elif path.suffix == '.json':
                json.loads(content)
            elif content.startswith('#!/bin/sh') or content.startswith('#!/bin/bash'):
                run(['bash', '-n', path], capture_output=True)
            for name, pattern in private_rules.items():
                if re.search(pattern, content):
                    failures.append(relative + ': ' + name)
        except (UnicodeError, SyntaxError, ValueError, subprocess.CalledProcessError) as error:
            failures.append(relative + ': ' + type(error).__name__)
    if failures:
        raise RuntimeError('\n'.join(failures))
    run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'), '-p', 'test_*.py'], cwd=ROOT)
    print(f'Checked {count} source files; syntax, privacy rules and unit checks passed.')


def build_guest(args):
    if platform.system() != 'Linux':
        raise RuntimeError('Build guest outputs inside the target Linux distro')
    out = BUILD / 'guest'
    out.mkdir(parents=True, exist_ok=True)
    cc = os.environ.get('CC', 'cc')
    def compile(output, sources, flags):
        run([cc, '-O2', *[ROOT / source for source in sources], *flags, '-o', out / output])
    compile('camera-readers', ['guest/devices/camera_readers.c'], [])
    compile('pam_linuxhost.so', ['guest/auth/pam_linuxhost.c'],
            ['-shared', '-fPIC', *pkg('json-c'), '-lpam'])
    from sources import Sources
    va_source = Sources().prepare('rockchip-vaapi') / 'src'
    compile('ashacky_drv_video.so', [va_source / 'rockchip_drv_video.c', va_source / 'h264.c', 'guest/video/graphics.c'],
            ['-shared', '-fPIC', '-I' + str(ROOT / 'guest/video'), '-Wno-deprecated-declarations', *pkg('libva', 'gbm', 'egl', 'glesv2'), '-pthread', '-ldl'])
    if args.ffmpeg_prefix:
        prefix = Path(args.ffmpeg_prefix).resolve(strict=True)
        # Remote H.264 requires the pinned, patched FFmpeg; stock FFmpeg is insufficient.
        library = prefix / 'lib'
        if not (prefix / 'include/libavcodec/avcodec.h').is_file():
            raise RuntimeError('Missing FFmpeg headers')
        import ctypes
        ctypes.CDLL(str(library / 'libavutil.so'), mode=ctypes.RTLD_GLOBAL)
        av = ctypes.CDLL(str(library / 'libavcodec.so'))
        av.avcodec_find_decoder_by_name.restype = ctypes.c_void_p
        av.avcodec_find_decoder_by_name.argtypes = [ctypes.c_char_p]
        if not av.avcodec_find_decoder_by_name(b'h264_videotoolbox_remote'):
            raise RuntimeError('This FFmpeg has no h264_videotoolbox_remote decoder')
        compile('decoder-worker-direct-va', ['guest/video/decoder-worker.c'],
                ['-I' + str(prefix / 'include'), '-L' + str(library),
                 '-Wl,-rpath,' + str(library), '-lavcodec', '-lavutil', '-llz4'])
    if args.kernel:
        for name in ('wifi', 'bluetooth', 'battery'):
            target = out / 'drivers' / name
            shutil.copytree(ROOT / 'guest/drivers' / name, target, dirs_exist_ok=True)
            run(['make', '-C', '/lib/modules/' + args.kernel + '/build', 'M=' + str(target), 'modules'])
    print('Guest build outputs:', out)
    if not args.ffmpeg_prefix:
        print('Broker omitted: provide --ffmpeg-prefix with the pinned remote-decoder build.')


def host_constants(config, out):
    uid, gid = config['userID'], config['groupID']
    if any(type(value) is not int or value <= 0 for value in (uid, gid)):
        raise ValueError('Host UID and GID must be non-root positive integers')
    paths = [config[name] for name in ('usbRuntime', 'powerRuntime', 'powerPolicy')]
    clients = config['powerClients']
    if not isinstance(clients, list) or not clients:
        raise ValueError('At least one exact control executable path is required')
    for path in [*paths, *clients]:
        if not isinstance(path, str) or not path.startswith('/') or re.search(r'[\x00-\x1f"\\]', path):
            raise ValueError('Expected an absolute path without control/escape characters')
    if len((config['usbRuntime'] + '/redirect.sock').encode()) >= 104:
        raise ValueError('USB Unix socket path is too long')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'installation.h').write_text(
        '#define ASHACKY_USER_ID ' + str(uid) + '\n#define ASHACKY_GROUP_ID ' + str(gid) +
        '\n#define ASHACKY_USB_DIRECTORY ' + json.dumps(config['usbRuntime'], ensure_ascii=False) + '\n')
    (out / 'Installation.swift').write_text('enum Installation {\n' + '\n'.join([
        '    static let powerRuntime = ' + json.dumps(config['powerRuntime'], ensure_ascii=False),
        '    static let powerPolicy = ' + json.dumps(config['powerPolicy'], ensure_ascii=False),
        '    static let powerClients = ' + json.dumps(clients, ensure_ascii=False),
    ]) + '\n}\n')


def build_host(args):
    if platform.system() != 'Darwin':
        raise RuntimeError('Build host outputs on macOS; this does not deploy them')
    out = BUILD / 'host'
    out.mkdir(parents=True, exist_ok=True)
    common = ROOT / 'host/common/IPC.swift'
    run(['swiftc', '-parse-as-library', '-swift-version', '5', '-O', common,
         ROOT / 'host/control/Control.swift', '-o', out / 'LinuxHostControl'])
    run(['swiftc', '-swift-version', '5', '-O', ROOT / 'host/session/SessionSync.swift', '-o', out / 'SessionSync'])
    run([out / 'SessionSync', '--self-test'])
    run(['clang', '-O2', '-c', ROOT / 'host/video/fence.c', '-o', out / 'fence.o'])
    run(['swiftc', '-swift-version', '5', '-O', ROOT / 'host/video/CodecShared.swift', out / 'fence.o',
         '-o', out / 'LinuxHostVideoShared'])
    for device in ('wifi', 'bluetooth', 'camera'):
        run(['bash', ROOT / f'host/devices/build-{device}-workbench.sh'])
    if args.installation:
        config = json.loads(Path(args.installation).read_text())
        generated = out / 'generated'
        host_constants(config, generated)
        run(['swiftc', '-parse-as-library', '-swift-version', '5', '-O', common,
             generated / 'Installation.swift', ROOT / 'helpers/power/PowerHelper.swift',
             '-o', out / 'LinuxHostPower'])
        run(['clang', '-O2', '-I' + str(generated), ROOT / 'helpers/usb/usb-host.c',
             *pkg('libusb-1.0', 'libusbredirhost'), '-framework', 'SystemConfiguration', '-framework', 'CoreFoundation',
             '-o', out / 'linuxhost-usb-host'])
    print('Host component outputs:', out)
    print('QEMU, the SPICE frontend and dependency bundle have separate recipes; see docs/BUILD.md.')


def inventory():
    print(json.dumps({'platform': platform.system(), 'architecture': platform.machine(),
        'uid': os.getuid(), 'python': platform.python_version(),
        'tools': {name: bool(shutil.which(name)) for name in
                  ('git', 'cc', 'pkg-config', 'swiftc', 'xcrun', 'meson', 'ninja', 'dkms')},
        'hostOutputs': (BUILD / 'host').is_dir(), 'guestOutputs': (BUILD / 'guest').is_dir()}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    commands.add_parser('inventory')
    build = commands.add_parser('build')
    build.add_argument('target', choices=('guest', 'host'))
    build.add_argument('--ffmpeg-prefix')
    build.add_argument('--kernel', help='guest kernel release whose headers are installed')
    build.add_argument('--installation', help='private host helper build configuration JSON')
    args = parser.parse_args()
    if args.command == 'check': check()
    elif args.command == 'inventory': inventory()
    elif args.target == 'guest': build_guest(args)
    else: build_host(args)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
