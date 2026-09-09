#!/usr/bin/env python3
"""Exercise a launcher with a temporary, windowless app; never opens a VM/device.

Requires an active macOS GUI account and Command Line Tools. The fixture has a
unique bundle identity, no permission requests, no services and a private runtime.
Its LaunchServices registration and temporary files are removed on completion.
"""
import argparse
import os
from pathlib import Path
import platform
import plistlib
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LSREGISTER = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher', type=Path, default=ROOT / 'build/host/Ashacky.app/Contents/MacOS/AshackyLauncher')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        raise RuntimeError('Launcher checks require macOS')
    with tempfile.TemporaryDirectory(prefix='launcher-check-', dir=ROOT / 'build') as temporary:
        directory = Path(temporary)
        app = directory / 'path with spaces/LauncherCheck.app'
        binaries = app / 'Contents/MacOS'
        binaries.mkdir(parents=True)
        launcher = binaries / 'AshackyLauncher'
        shutil.copy2(args.launcher, launcher)
        fixture = binaries / 'LauncherCheck'
        subprocess.run(['swiftc', '-swift-version', '5', '-O', str(ROOT / 'tests/launcher-fixture.swift'),
                        '-o', str(fixture)], check=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'org.ashacky.launcher-check.' + uuid.uuid4().hex,
            'CFBundleExecutable': 'LauncherCheck', 'CFBundleName': 'LauncherCheck',
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'LSBackgroundOnly': True}))
        subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('ASHACKY_', 'LINUXHOST_', 'LH_VIDEO_', 'DYLD_'))}
        children = []
        app_pids = set()

        def run_case(name, mode='hold', stop=None, invalid_runtime=False):
            case = directory / name
            runtime = case / 'runtime'
            runtime.mkdir(parents=True, mode=0o700)
            if invalid_runtime:
                runtime.chmod(0o755)
            child = subprocess.Popen([str(launcher), mode],
                env=env | {'ASHACKY_PRIVATE_DIRECTORY': str(case), 'ASHACKY_LAUNCHER_CHECK_DIRECTORY': str(case)},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            children.append(child)
            started = time.monotonic()
            if stop:
                deadline = started + 8
                marker = case / ('born' if stop == 'during-launch' else 'ready')
                while not marker.exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(name + ': fixture did not become ready')
                    time.sleep(.02)
                pid = int(marker.read_text())
                app_pids.add(pid)
                if stop == 'idle':
                    # No shutdown deadline is active while the app is running.
                    time.sleep(3.5)
                    assert child.poll() is None
                child.send_signal(signal.SIGINT if stop == 'interrupt' else signal.SIGTERM)
                started = time.monotonic()
                if stop == 'repeat':
                    for _ in range(4):
                        time.sleep(.5)
                        child.send_signal(signal.SIGTERM)
            out, error = child.communicate(timeout=10)
            elapsed = time.monotonic() - started
            expected = 1 if mode in ('dirty', 'crash') or invalid_runtime or name == 'launch-failure' else 0
            assert child.returncode == expected, (name, child.returncode, out, error)
            assert not list(runtime.glob('display-exit-*')), name + ': stale exit receipt'
            marker = case / ('born' if stop == 'during-launch' else 'ready')
            if marker.exists():
                pid = int(marker.read_text()); app_pids.add(pid)
                # LaunchServices has reaped the app before the launcher exits.
                try: os.kill(pid, 0)
                except ProcessLookupError: pass
                else: raise AssertionError(name + ': app outlived its launcher')
                app_pids.discard(pid)
            if mode == 'refuse':
                assert 2.7 <= elapsed < 4.5, (name, elapsed)
            print(name + ': passed', flush=True)

        try:
            run_case('invalid-runtime', invalid_runtime=True)
            for index in range(3):
                run_case('fast-clean-' + str(index), 'clean')
            run_case('dirty-exit', 'dirty')
            run_case('crash', 'crash')
            run_case('graceful-stop', stop='terminate')
            run_case('interrupt-stop', stop='interrupt')
            run_case('idle-then-stop', stop='idle')
            run_case('stop-during-launch', 'delayed-launch', stop='during-launch')
            run_case('forced-stop', 'refuse', stop='terminate')
            run_case('repeated-stop', 'refuse', stop='repeat')
            fixture.unlink()
            run_case('launch-failure')
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    try: child.wait(timeout=5)
                    except subprocess.TimeoutExpired: child.kill(); child.wait()
            # Only fixture PIDs recorded by our private app are eligible.
            for pid in app_pids:
                command = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'comm='], capture_output=True, text=True)
                if command.stdout.strip() == str(fixture):
                    try: os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError: pass
            subprocess.run([LSREGISTER, '-u', str(app)], check=False, capture_output=True)
        print('Launcher exit/crash, idle, stop deadline and launch-failure checks passed; fixture removed.')


if __name__ == '__main__':
    main()
