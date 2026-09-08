#!/usr/bin/env python3
"""Build the macOS runtime in build/; never install services or start a VM."""
import argparse
import bz2
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys

from sources import Sources
from utm_assets import UTMAssets

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'


class RuntimeBuild:
    def __init__(self, jobs):
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise RuntimeError('Build this runtime on an Apple Silicon macOS host')
        self.jobs = str(jobs)
        self.sources = Sources()
        self.work = BUILD / 'runtime'
        self.prefix = self.work / 'prefix'
        self.python = BUILD / 'python/bin/python3'
        for directory in (self.work, self.prefix, BUILD / 'host'):
            if directory.is_symlink():
                raise RuntimeError('Build output must not redirect to an installed runtime: ' + str(directory))
            directory.mkdir(parents=True, exist_ok=True)
        # Refuse to overwrite any running build output. This is an inspection;
        # no process is stopped and no installed path is changed.
        opened = subprocess.run(['/usr/sbin/lsof', '-t', '+D', str(BUILD / 'host')],
                                capture_output=True, text=True)
        if opened.stdout.strip():
            raise RuntimeError('Host build outputs are in use; use an isolated validation checkout')
        brew = subprocess.check_output(['brew', '--prefix'], text=True).strip()
        self.brew = Path(brew)
        self.env = dict(os.environ)
        for name in ('CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'CPATH', 'LIBRARY_PATH',
                     'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'):
            self.env.pop(name, None)
        self.env['PATH'] = os.pathsep.join(map(str, [BUILD / 'python/bin', self.brew / 'opt/bison/bin',
            self.brew / 'bin', Path('/usr/bin'), Path('/bin'), Path('/usr/sbin'), Path('/sbin')]))
        defaults = subprocess.check_output(['pkg-config', '--variable=pc_path', 'pkg-config'],
            env={k: v for k, v in self.env.items() if not k.startswith('PKG_CONFIG')}, text=True).strip().split(os.pathsep)
        pcs = [self.prefix / 'lib/pkgconfig', self.prefix / 'share/pkgconfig',
               self.brew / 'opt/openssl@3/lib/pkgconfig', self.brew / 'opt/libffi/lib/pkgconfig',
               self.brew / 'lib/pkgconfig', self.brew / 'share/pkgconfig']
        # Homebrew supplies SDK shims such as zlib.pc outside lib/pkgconfig.
        pcs += [Path(path) for path in defaults if path and Path(path).is_relative_to(self.brew)]
        self.env['PKG_CONFIG_PATH'] = self.env['PKG_CONFIG_LIBDIR'] = os.pathsep.join(map(str, pcs))
        self.env['CFLAGS'] = self.env['CXXFLAGS'] = '-O2 -Wno-error=deprecated-declarations'
        self.env['LDFLAGS'] = '-Wl,-headerpad_max_install_names'
        self.env['MACOSX_DEPLOYMENT_TARGET'] = '13.0'

    def run(self, command, cwd=ROOT):
        print('+ ' + shlex.join(map(str, command)), flush=True)
        subprocess.run(list(map(str, command)), cwd=cwd, env=self.env, check=True)

    def bootstrap(self):
        if not self.python.exists():
            self.run([sys.executable, '-m', 'venv', BUILD / 'python'])
        self.run([self.python, '-m', 'pip', 'install', '--disable-pip-version-check',
                  '-r', ROOT / 'upstream/build-requirements.txt'])
        packages = ['glib-2.0', 'gio-2.0', 'pixman-1', 'libjpeg', 'openssl', 'opus',
                    'liblz4', 'libzstd', 'libusb-1.0', 'libusbredirhost', 'json-glib-1.0']
        self.run(['pkg-config', '--print-errors', '--exists', *packages])
        versions = subprocess.check_output(['pkg-config', '--modversion', *packages],
                                           env=self.env, text=True).splitlines()
        report = {'python': sys.version, 'packages': dict(zip(packages, versions)),
                  'developerDirectory': subprocess.check_output(['xcode-select', '-p'], text=True).strip(),
                  'clang': subprocess.check_output(['clang', '--version'], text=True),
                  'brew': subprocess.check_output(['brew', 'list', '--versions'], text=True,
                              env=dict(self.env, HOMEBREW_NO_AUTO_UPDATE='1'))}
        (self.work / 'toolchain.json').write_text(json.dumps(report, indent=2) + '\n')

    def meson(self, name, options, targets=None, install=True):
        source = self.sources.prepare(name)
        output = self.work / name
        command = ['meson', 'setup', output, source, '--prefix=' + str(self.prefix),
                   '--libdir=lib', '--buildtype=release', '--wrap-mode=nofallback',
                   '-Dauto_features=disabled', '-Ddefault_library=shared', *options]
        if (output / 'build.ninja').exists():
            command.insert(2, '--reconfigure')
        self.run(command)
        self.run(['ninja', '-C', output, '-j', self.jobs, *(targets or [])])
        if install:
            self.run(['meson', 'install', '-C', output, '--no-rebuild'])
        return output

    def graphics(self):
        assets = UTMAssets().prepare()
        frameworks = assets / 'Frameworks'
        include = self.prefix / 'include'
        include.mkdir(exist_ok=True)
        angle = self.sources.prepare('angle-webkit') / 'Source/ThirdParty/ANGLE/include'
        for name in ('EGL', 'GLES2', 'GLES3', 'KHR'):
            shutil.copytree(angle / name, include / name, dirs_exist_ok=True)
        epoxy = self.sources.prepare('libepoxy')
        headers = include / 'epoxy'
        headers.mkdir(exist_ok=True)
        for name in ('common.h', 'gl.h', 'egl.h'):
            shutil.copy2(epoxy / 'include/epoxy' / name, headers / name)
        for name in ('gl', 'gl_angle_ext', 'egl', 'egl_angle_ext'):
            self.run([self.python, epoxy / 'src/gen_dispatch.py', '--header', '--no-source',
                      '--outputdir=' + str(headers), epoxy / 'registry' / (name + '.xml')])
        virgl = self.sources.prepare('virglrenderer')
        shutil.copy2(virgl / 'src/virglrenderer.h', include / 'virglrenderer.h')
        version = (virgl / 'src/virgl-version.h.meson').read_text()
        for name, value in [('MAJOR', '1'), ('MINOR', '3'), ('MICRO', '0')]:
            version = version.replace('@VIRGL_' + name + '_VERSION@', value)
        (include / 'virgl-version.h').write_text(version)
        pcs = self.prefix / 'lib/pkgconfig'
        pcs.mkdir(parents=True, exist_ok=True)
        for name, version, framework in [('epoxy', '1.5.9', 'epoxy.0'),
                ('virglrenderer', '1.3.0', 'virglrenderer.1'), ('vulkan', '1.4.0', 'vulkan.1')]:
            # pkg-config quotes preserve paths containing spaces.
            (pcs / (name + '.pc')).write_text(
                'epoxy_has_egl=1\nepoxy_has_glx=0\nName: ' + name +
                '\nDescription: Pinned UTM release framework\nVersion: ' + version +
                '\nLibs: -F' + shlex.quote(str(frameworks)) + ' -framework ' + framework +
                ' -Wl,-rpath,' + shlex.quote(str(frameworks)) +
                '\nCflags: -I' + shlex.quote(str(include)) + '\n')

    def dependencies(self):
        self.bootstrap()
        self.graphics()
        self.meson('spice-protocol', [])
        self.meson('spice-server', ['-Dgstreamer=no', '-Dsasl=false', '-Dsmartcard=disabled',
                                  '-Dmanual=false', '-Dopus=enabled'])
        self.meson('gstreamer', ['-Dtools=enabled'])
        self.meson('gst-plugins-base', ['-D' + name + '=enabled' for name in
            ('app', 'audioconvert', 'audioresample', 'audiotestsrc', 'volume', 'typefind', 'playback')])
        self.meson('gst-plugins-good', ['-Dosxaudio=enabled', '-Dautodetect=enabled', '-Dlevel=enabled'])
        self.meson('spice-gtk', ['-Dcoroutine=gthread', '-Dopus=enabled', '-Dlz4=enabled',
                                 '-Dusbredir=enabled'])

    def render_server(self):
        # The upstream target links private renderer internals into the server;
        # compiling main.c alone against the public framework is insufficient.
        output = self.meson('virglrenderer', ['-Dvenus=true', '-Dneptune=true', '-Dtests=false',
            '-Dvtest=false', '-Dcheck-gl-errors=false', '-Dvulkan-dload=false',
            '-Drender-server-mode=process', '-Drender-server-worker=process'],
            targets=['server/virgl_render_server'], install=False)
        shutil.copy2(output / 'server/virgl_render_server', BUILD / 'host/virgl_render_server')

    def qemu(self):
        source = self.sources.prepare('qemu')
        output = self.work / 'qemu'
        output.mkdir(exist_ok=True)
        self.run([source / 'configure', '--prefix=' + str(self.prefix),
            '--target-list=aarch64-softmmu', '--without-default-features', '--enable-hvf',
            '--enable-cocoa', '--enable-opengl', '--enable-virglrenderer', '--enable-vmnet',
            '--enable-spice', '--enable-spice-protocol', '--enable-usb-redir', '--enable-virtfs',
            '--enable-tools', '--enable-pixman', '--disable-docs', '--disable-werror'], cwd=output)
        self.run(['ninja', '-C', output, '-j', self.jobs, 'qemu-system-aarch64', 'qemu-img'])
        for name in ('qemu-system-aarch64', 'qemu-img'):
            shutil.copy2(output / name, BUILD / 'host' / name)
        self.run(['codesign', '--force', '--sign', '-', '--entitlements',
                  ROOT / 'host/frontend/hypervisor-entitlements.plist', BUILD / 'host/qemu-system-aarch64'])
        self.run([BUILD / 'host/qemu-system-aarch64', '--version'])
        firmware = json.loads((ROOT / 'upstream/firmware.json').read_text())
        code = bz2.decompress((source / firmware['code']).read_bytes())
        if hashlib.sha256(code).hexdigest() != firmware['uncompressedSHA256']:
            raise RuntimeError('Firmware differs from its recorded hash')
        (BUILD / 'host/edk2-aarch64-code.fd').write_bytes(code)
        shutil.copy2(source / firmware['licenses'], BUILD / 'host/edk2-licenses.txt')

    def network(self):
        source = self.sources.prepare('socket-vmnet')
        version = subprocess.check_output(['git', '-C', str(source), 'describe', '--always', '--tags'], text=True).strip()
        self.run(['clang', '-O3', '-Wall', '-Wextra', '-pedantic', '-DVERSION="' + version + '"',
                  *sorted(source.glob('*.c')), '-framework', 'vmnet', '-o', BUILD / 'host/socket_vmnet'])
        self.run(['clang', '-O3', '-Wall', '-Wextra', '-pedantic', '-DVERSION="' + version + '"',
                  *sorted((source / 'client').glob('*.c')), '-o', BUILD / 'host/socket_vmnet_client'])
        self.run(['swiftc', '-O', ROOT / 'host/transport/wifi-interface.swift',
                  '-o', BUILD / 'host/wifi-interface'])

    def frontend(self):
        self.run([self.python, ROOT / 'tools/build-frontend.py'])

    def helpers(self):
        self.run([self.python, ROOT / 'tools/ashacky.py', 'build', 'host'])
        source = self.sources.prepare('videotoolbox-remote')
        self.run(['swift', 'build', '--package-path', source / 'vtremoted',
                  '--scratch-path', BUILD / 'host/vtremoted-build', '-c', 'release', '-j', self.jobs])
        shutil.copy2(BUILD / 'host/vtremoted-build/release/vtremoted', BUILD / 'host/vtremoted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('all', 'deps', 'qemu', 'render-server', 'network', 'frontend', 'helpers'))
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    build = RuntimeBuild(args.jobs)
    stages = ['deps', 'render-server', 'qemu', 'network', 'frontend', 'helpers'] if args.stage == 'all' else [args.stage]
    for stage in stages:
        getattr(build, {'deps': 'dependencies', 'render-server': 'render_server'}.get(stage, stage))()
    print('Runtime build complete. Nothing was installed or started.')


if __name__ == '__main__':
    main()
