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
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('assembly', ROOT / 'tools/assemble-runtime.py')
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / 'build/host/Ashacky.app')
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
