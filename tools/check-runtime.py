#!/usr/bin/env python3
"""Check a relocated runtime without starting a VM, services, or hardware capture."""
import argparse
import ctypes
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('assembly', ROOT / 'tools/assemble-runtime.py')
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / 'build/host/Ashacky.app')
    parser.add_argument('--spice-build', type=Path, default=ROOT / 'build/runtime/spice-gtk',
                        help='Matching configured SPICE client build for its transport fixture')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        raise RuntimeError('Check the runtime on macOS')
    with tempfile.TemporaryDirectory(prefix='relocation-check-', dir=ROOT / 'build/runtime') as directory:
        temporary = Path(directory)
        app = temporary / 'path with spaces/Ashacky.app'
        shutil.copytree(args.app, app, symlinks=True)
        count = assembly.inspect_bundle(app)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        contents = app / 'Contents'
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('DYLD_', 'GST_', 'VK_', 'LINUXHOST_', 'LH_VIDEO_'))}
        env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin',
            GST_PLUGIN_PATH=str(contents / 'Resources/gstreamer-1.0'), GST_PLUGIN_SYSTEM_PATH='',
            GST_REGISTRY=str(temporary / 'gst-registry.bin'), GST_REGISTRY_FORK='no')

        def run(name, *arguments):
            return subprocess.check_output([str(contents / 'MacOS' / name), *arguments],
                env=env, text=True, stderr=subprocess.STDOUT, timeout=60)

        print(run('qemu-system-aarch64', '--version').splitlines()[0])
        print(run('qemu-img', '--version').splitlines()[0])
        print(run('Ashacky', '--check-display-transport').strip())
        subprocess.run([sys.executable, str(ROOT / 'tools/check-spice-client.py'),
            '--app', str(app), '--spice-build', str(args.spice_build)], check=True, timeout=30)
        for obsolete in ('LinuxHostControl', 'SessionSync'):
            if (contents / 'MacOS' / obsolete).exists():
                raise RuntimeError('Obsolete standalone session helper: ' + obsolete)
        session_check = temporary / 'session-service-checks'
        subprocess.run(['swiftc', '-parse-as-library', '-swift-version', '5', '-O',
            *[str(ROOT / name) for name in ('host/common/IPC.swift', 'host/control/Control.swift', 'host/control/PowerObserver.swift',
                'host/session/ControlChannel.swift', 'host/session/SessionServices.swift',
                'host/auth/CBOR.swift', 'host/auth/FIDO2.swift', 'host/auth/FIDO2Service.swift',
                'tests/control-channel.swift', 'tests/fido2-store.swift', 'tests/session-services.swift')],
            '-o', str(session_check)], check=True)
        subprocess.run([str(session_check)], check=True, timeout=10)
        peer = temporary / 'control-peer'
        subprocess.run(['swiftc', '-parse-as-library', '-swift-version', '5', '-O',
            str(ROOT / 'host/session/ControlChannel.swift'), str(ROOT / 'tests/control-peer.swift'),
            '-o', str(peer)], check=True)
        subprocess.run([sys.executable, str(ROOT / 'tools/check-control.py'), '--peer', str(peer)],
                       check=True, timeout=15)
        camera_object = temporary / 'camera-memory.o'
        camera_library = temporary / 'camera-memory.dylib'
        subprocess.run(['clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-c',
            str(ROOT / 'host/common/CameraMemory.c'), '-o', str(camera_object)], check=True)
        subprocess.run(['clang', '-dynamiclib', str(camera_object), '-o', str(camera_library)], check=True)
        camera_check = temporary / 'camera-check'
        subprocess.run(['swiftc', '-parse-as-library', '-swift-version', '5', '-O',
            *[str(ROOT / name) for name in ('host/common/IPC.swift', 'host/devices/CameraBuffer.swift', 'tests/camera-memory.swift')],
            str(camera_object), '-o', str(camera_check)], check=True)
        camera_memory = temporary / 'camera-frames.bin'
        with camera_memory.open('wb') as f: f.truncate(8 * 1024 * 1024)
        subprocess.run([str(camera_check), str(camera_memory)], check=True, timeout=10)
        subprocess.run([sys.executable, str(ROOT / 'tools/check-camera.py'), '--peer', str(camera_check),
            '--memory', str(camera_memory), '--library', str(camera_library)], check=True, timeout=15)
        subprocess.run([sys.executable, str(ROOT / 'tools/check-launcher.py'),
            '--launcher', str(contents / 'MacOS/AshackyLauncher')], check=True, timeout=90)
        assert 'hvf' in run('qemu-system-aarch64', '-accel', 'help')
        devices = run('qemu-system-aarch64', '-device', 'help')
        for device in ('virtio-ramfb-gl', 'usb-redir', 'linuxhost-shmem', 'virtio-9p-pci'):
            if '"' + device + '"' not in devices:
                raise RuntimeError('Required QEMU device is missing: ' + device)
        gpu = run('qemu-system-aarch64', '-device', 'virtio-ramfb-gl,help')
        for name in ('venus', 'neptune', 'blob', 'hostmem'):
            if name not in gpu:
                raise RuntimeError('Required GPU property is missing: ' + name)
        print('HVF, SPICE GPU properties, shared memory, USB redirection and 9p are present.')
        for element in ('audiotestsrc', 'audioconvert', 'audioresample', 'appsink', 'appsrc',
                        'osxaudiosink', 'osxaudiosrc', 'level', 'fakesink'):
            run('gst-inspect-1.0', element)
        run('gst-launch-1.0', '-q', 'audiotestsrc', 'num-buffers=2', '!', 'audioconvert',
            '!', 'audioresample', '!', 'fakesink')
        print('Audio plugins and a silent in-memory audio pipeline passed.')
        for name in ('EGL', 'GLESv2', 'epoxy.0', 'vulkan.1', 'virglrenderer.1', 'MoltenVK'):
            ctypes.CDLL(str(contents / 'Frameworks' / (name + '.framework') / name))
        for name, symbol in [('liblz4.1.dylib', 'LZ4_decompress_safe'), ('libzstd.1.dylib', 'ZSTD_decompress')]:
            library = ctypes.CDLL(str(contents / 'Frameworks' / name))
            getattr(library, symbol)
        shader = temporary / 'metal-shader-check'
        subprocess.run(['clang', '-fobjc-arc', '-O2', str(ROOT / 'tests/metal-shader.m'),
                        '-framework', 'Foundation', '-framework', 'Metal', '-o', str(shader)], check=True)
        subprocess.run([str(shader), str(contents / 'Resources/CocoaSpice_CocoaSpiceRenderer.bundle')],
                       env=env, check=True)
        print(f'Relocation, {count} Mach-O dependency closures, libraries and shader passed. No VM or service started.')


if __name__ == '__main__':
    main()
