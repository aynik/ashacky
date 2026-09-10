#!/usr/bin/env python3
"""Build the CocoaSpice frontend with Command Line Tools and a pinned UTM shader.

Needs a patched CocoaSpice checkout and matching SPICE/GStreamer pkg-config paths.
Does not stage QEMU or its graphics dependency closure; see docs/BUILD.md.
"""
import argparse
import os
from pathlib import Path
import platform
import plistlib
import shlex
import shutil
import subprocess
from sources import Sources
from utm_assets import UTMAssets

ROOT = Path(__file__).resolve().parents[1]


def run(command):
    subprocess.run([str(item) for item in command], check=True)


def build_icon(resources, objects):
    source = ROOT / 'host/frontend/assets/Ashacky.png'
    iconset = objects / 'Ashacky.iconset'
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = '@2x' if scale == 2 else ''
            target = iconset / f'icon_{size}x{size}{suffix}.png'
            pixels = size * scale
            run(['/usr/bin/sips', '-z', pixels, pixels, source, '--out', target])
    run(['/usr/bin/iconutil', '--convert', 'icns', '--output', resources / 'Ashacky.icns', iconset])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compile-shaders', action='store_true',
                        help='optional source shader build; requires the Metal compiler')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/host',
                        help='build directory inside build/; use a temporary directory to validate while the installed app runs')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        raise RuntimeError('The frontend must be built on macOS')
    output = args.output.absolute()
    if output.is_symlink() or not output.resolve().is_relative_to((ROOT / 'build').resolve()):
        raise RuntimeError('Frontend output must stay inside this checkout\'s build directory')
    if args.compile_shaders:
        run(['xcrun', '--find', 'metal'])
        run(['xcrun', '--find', 'metallib'])
    dependencies = Sources()
    sources = dependencies.prepare('cocoaspice') / 'Sources'
    keymap = dependencies.keymap()
    assets = UTMAssets()
    if not args.compile_shaders:
        assets.verify_shader_sources(sources)
        upstream = assets.prepare()
    objects = output / 'frontend-objects'
    objects.mkdir(parents=True, exist_ok=True)
    contents = output / 'Ashacky.app/Contents'
    opened = subprocess.run(['/usr/sbin/lsof', '-t', '+D', str(contents.parent)],
                            capture_output=True, text=True)
    if opened.stdout.strip() or contents.parent.is_symlink():
        raise RuntimeError('Refusing to rebuild a running or redirected app')
    (contents / 'MacOS').mkdir(parents=True, exist_ok=True)
    resources = contents / 'Resources'
    resources.mkdir(parents=True, exist_ok=True)
    build_icon(resources, objects)
    bundle = resources / 'CocoaSpice_CocoaSpiceRenderer.bundle/Contents'
    if bundle.parent.exists():
        shutil.rmtree(bundle.parent)
    if args.compile_shaders:
        (bundle / 'Resources').mkdir(parents=True, exist_ok=True)
        (bundle / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'local.ashacky.renderer',
            'CFBundleName': 'CocoaSpiceRenderer', 'CFBundlePackageType': 'BNDL'}))
        run(['xcrun', '-sdk', 'macosx', 'metal', '-c', sources / 'CocoaSpiceRenderer/CSShaders.metal',
             '-I', sources / 'CocoaSpiceRenderer/include', '-o', objects / 'shaders.air'])
        run(['xcrun', '-sdk', 'macosx', 'metallib', objects / 'shaders.air', '-o', bundle / 'Resources/default.metallib'])
    else:
        shutil.copytree(upstream / assets.lock['shaderBundle'], bundle.parent, symlinks=True)
    # Make the Clang module map a generated build input, never edit upstream source.
    modulemap = objects / 'module.modulemap'
    header = sources / 'CocoaSpiceRenderer/include/CocoaSpiceRenderer.h'
    modulemap.write_text('module CocoaSpiceRenderer { umbrella header "' + str(header) + '" export * }\n')
    gst = objects / 'gst-init.m'
    gst.write_text('#include <gst/gst.h>\nvoid gst_ios_init(void) { gst_init(NULL,NULL); }\n')
    packages = ['glib-2.0', 'gio-2.0', 'gobject-2.0', 'gstreamer-1.0', 'spice-client-glib-2.0', 'libusb-1.0']
    def pkg(option):
        return shlex.split(subprocess.check_output(['pkg-config', option, *packages], text=True))
    flags = pkg('--cflags') + ['-DWITH_USB_SUPPORT', '-fobjc-arc', '-fmodules', '-O2',
        '-Wno-nullability-completeness', '-fmodule-map-file=' + str(modulemap)]
    for include in (sources / 'CocoaSpice/include', sources / 'CocoaSpiceRenderer/include',
                    sources / 'CocoaSpice', ROOT / 'host/frontend', keymap.parent):
        flags.append('-I' + str(include))
    inputs = sorted(p for p in (sources / 'CocoaSpice').glob('*.m') if p.name != 'gst_ios_init.m')
    inputs += sorted((sources / 'CocoaSpiceRenderer').glob('*.m'))
    inputs += [gst, ROOT / 'host/frontend/main.m']
    host_sources = [ROOT / 'host/common/IPC.swift', *[ROOT / 'host/devices' / name for name in
        ('WiFiBackend.swift', 'WiFiService.swift', 'AudioBackend.swift', 'BluetoothService.swift',
         'CameraBuffer.swift', 'CameraService.swift', 'HostServices.swift', 'SidecarService.swift')]]
    host_sources += [ROOT / name for name in ('host/control/Control.swift', 'host/control/PowerObserver.swift',
        'host/session/ControlChannel.swift', 'host/session/SessionServices.swift',
        'host/auth/CBOR.swift', 'host/auth/FIDO2.swift', 'host/auth/FIDO2Service.swift')]
    host_object = objects / 'host-services.o'
    run(['swiftc', '-parse-as-library', '-swift-version', '5', '-O', '-whole-module-optimization',
         '-module-name', 'AshackyHost', '-emit-object', '-emit-objc-header',
         '-emit-objc-header-path', objects / 'AshackyHost-Swift.h',
         '-emit-module-path', objects / 'AshackyHost.swiftmodule',
         *host_sources, '-o', host_object])
    flags.append('-I' + str(objects))
    camera_object = objects / 'camera-memory.o'
    run(['clang', '-std=c11', '-O2', '-c', ROOT / 'host/common/CameraMemory.c', '-o', camera_object])
    compiled = [host_object, camera_object]
    for index, source in enumerate(inputs):
        obj = objects / f'{index}-{source.stem}.o'
        run(['clang', *flags, '-c', source, '-o', obj])
        compiled.append(obj)
    libraries = pkg('--libs')
    for framework in ('Cocoa', 'Metal', 'MetalKit', 'CoreGraphics', 'IOSurface', 'AVFoundation', 'AudioToolbox',
                      'CoreLocation', 'CoreWLAN', 'CoreBluetooth', 'IOBluetooth', 'SystemConfiguration', 'CoreAudio',
                      'LocalAuthentication', 'IOKit', 'CoreServices', 'Security', 'CryptoKit'):
        libraries += ['-framework', framework]
    # Swift's linker driver includes the Swift runtime required by the services.
    link_flags = []
    for flag in libraries:
        link_flags += ['-Xlinker', flag]
    run(['swiftc', *compiled, *link_flags, '-o', contents / 'MacOS/Ashacky'])
    run(['swiftc', '-swift-version', '5', '-O', ROOT / 'host/frontend/Launcher.swift',
         '-o', contents / 'MacOS/AshackyLauncher'])
    run(['swiftc', '-swift-version', '5', '-O', '-import-objc-header', ROOT / 'host/common/DisplayLayout.h',
         ROOT / 'host/devices/SidecarBackend.swift',
         ROOT / 'tools/sidecar-probe.swift', '-o', contents / 'MacOS/AshackySidecar'])
    # Retire the old frontend name when rebuilding an existing output directory.
    (contents / 'MacOS/LinuxHostSPICE').unlink(missing_ok=True)
    for name in ('LinuxHostControl', 'SessionSync'):
        (contents / 'MacOS' / name).unlink(missing_ok=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'local.ashacky.host',
        'CFBundleName': 'Ashacky', 'CFBundleDisplayName': 'Ashacky', 'CFBundleExecutable': 'Ashacky', 'CFBundlePackageType': 'APPL',
        'CFBundleIconFile': 'Ashacky.icns',
        'CFBundleVersion': '1', 'LSMinimumSystemVersion': '12.0', 'NSHighResolutionCapable': True,
        'NSLocalNetworkUsageDescription': 'Communicate with your Linux virtual machine for device and session integration.',
        'NSMicrophoneUsageDescription': 'Send microphone audio to your Linux virtual machine.',
        'NSCameraUsageDescription': 'Send live camera video to your Linux virtual machine when requested.',
        'NSLocationUsageDescription': 'Discover Wi-Fi networks and manage the connection from Linux.',
        'NSLocationWhenInUseUsageDescription': 'Discover Wi-Fi networks and manage the connection from Linux.',
        'NSBluetoothAlwaysUsageDescription': 'Discover, pair and connect Bluetooth devices from Linux.',
        'NSBluetoothPeripheralUsageDescription': 'Discover, pair and connect Bluetooth devices from Linux.'}))
    print(contents.parent)
    print('Stage the dependency closure and sign the complete bundle before use; see docs/BUILD.md.')


if __name__ == '__main__':
    main()
