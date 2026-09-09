#!/usr/bin/env python3
"""Stage a Debian ARM64 guest payload under build/ without installing anything."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg-prefix', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--patchelf', default='patchelf')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('This payload adapter currently supports Debian ARM64 only')
    triplet = subprocess.check_output(['dpkg-architecture', '-qDEB_HOST_MULTIARCH'], text=True).strip()
    if triplet != 'aarch64-linux-gnu':
        raise RuntimeError('Unexpected Debian library architecture')
    subprocess.run([args.patchelf, '--version'], check=True, capture_output=True)
    prefix = args.ffmpeg_prefix.resolve(strict=True)
    plan = args.plan.resolve(strict=True)
    identity = json.loads((plan / 'identity.json').read_text())
    output = ROOT / 'build/guest-root'
    os.umask(0o077)
    output.mkdir(mode=0o700, exist_ok=False)

    def copy(source, target, mode=0o644):
        relative = Path(target).relative_to('/')
        if '..' in relative.parts:
            raise ValueError('Invalid guest target')
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path)
        path.chmod(mode)
        return path

    layout = json.loads((ROOT / 'guest/layout.json').read_text())
    for item in layout['files']:
        copy(ROOT / item['source'], item['target'], int(item['mode'], 8))
    for item in json.loads((plan / 'manifest.json').read_text()):
        if item['file'].startswith('guest/'):
            copy(plan / item['file'], item['target'], int(item['mode'], 8))
    for name, target in [('pam_linuxhost.so', f'/usr/lib/{triplet}/security/pam_linuxhost.so'),
                         ('ashacky_drv_video.so', f'/usr/lib/{triplet}/dri/ashacky_drv_video.so')]:
        copy(ROOT / 'build/guest' / name, target)
    (output / 'opt/linuxhost-video/lib').mkdir(parents=True, exist_ok=True)
    for source in (prefix / 'lib').glob('*.so*'):
        target = output / 'opt/linuxhost-video/lib' / source.name
        if source.is_symlink():
            # The FFmpeg SONAME aliases must stay within the private directory.
            link = os.readlink(source)
            if Path(link).name != link:
                raise RuntimeError('Unexpected FFmpeg library symlink')
            target.symlink_to(link)
        else:
            copy(source, '/opt/linuxhost-video/lib/' + source.name)
            subprocess.run([args.patchelf, '--set-rpath', '$ORIGIN', str(target)], check=True)
    broker = output / 'opt/linuxhost-video/bin/decoder-worker-direct-va'
    subprocess.run([args.patchelf, '--set-rpath', '$ORIGIN/../lib', str(broker)], check=True)
    for driver, package in [('wifi', 'linuxhost-wifi'), ('bluetooth', 'linuxhost-bt-radio'), ('battery', 'linuxhost-battery')]:
        for source in (ROOT / 'guest/drivers' / driver).iterdir():
            if source.suffix in ('.c', '.h') or source.name in ('Makefile', 'dkms.conf'):
                copy(source, '/usr/src/' + package + '-0.1.0/' + source.name)
    # These are review fragments; merging distro-owned settings is an explicit
    # installation step, never a side effect of staging.
    actions = {'guestUser': identity['guestUser'], 'guestUID': identity['guestUID'],
        'pamAuthLine': 'auth sufficient pam_linuxhost.so user=' + identity['guestUser'],
        'gdmDaemonSettings': {'AutomaticLoginEnable': True, 'AutomaticLogin': identity['guestUser']},
        'vaAlias': f'/usr/lib/{triplet}/dri/virtio_gpu_drv_video.so',
        'vaTarget': 'ashacky_drv_video.so',
        'dkmsPackages': ['linuxhost-wifi', 'linuxhost-bt-radio', 'linuxhost-battery'],
        'systemServices': [p.name for p in (ROOT / 'guest/systemd/system').glob('*.service')],
        'optionalSystemServices': {'ashacky-fido2.service': 'Disabled until host FIDO2 and attended acceptance are enabled; see docs/FIDO2.md'},
        'userServices': ['linuxhost-audio-pipewire.service', 'linuxhost-video-direct.socket']}
    manifest = []
    for path in sorted(output.rglob('*')):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o755)
            manifest.append({'target': '/' + str(path.relative_to(output)), 'owner': 'root:root',
                             'mode': '0o755', 'directory': True})
        elif path.is_file() or path.is_symlink():
            item = {'target': '/' + str(path.relative_to(output)), 'owner': 'root:root',
                    'mode': oct(path.lstat().st_mode & 0o777)}
            if path.is_symlink():
                item['link'] = os.readlink(path)
            else:
                item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest.append(item)
    (ROOT / 'build/guest-actions.json').write_text(json.dumps(actions, indent=2) + '\n')
    (ROOT / 'build/guest-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Staged Debian ARM64 payload:', output)
    print('Review guest-manifest.json and guest-actions.json; no packages, modules or services were installed.')


if __name__ == '__main__':
    main()
