#!/usr/bin/python3
"""Own and recover the configured guest's temporary camera SSH forward."""
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from vm_session import settings

ROOT = Path(__file__).resolve().parent.parent
STOP = threading.Event()


def main():
    os.chdir(ROOT)
    os.umask(0o077)
    private = settings()['privateDirectory']
    with open(private / 'probe-device-service.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another probe device service is running')
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: STOP.set())
        names = ('camera',)
        command = ['/bin/bash', str(Path(__file__).with_name('probe-ssh')), '-N',
                   '-o', 'ExitOnForwardFailure=yes',
                   '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=3']
        for name in names:
            command += ['-R', f'/run/linuxhost-{name}-test.sock:{private}/{name}-workbench.sock']
        retry_delay = 3
        while not STOP.is_set():
            if not all((private / f'{name}-workbench.sock').is_socket() for name in names):
                STOP.wait(3)
                continue
            # probe-ssh checks the pinned SSH host key and VM UUID first.
            started = time.monotonic()
            process = subprocess.Popen(command, start_new_session=True)
            print(f'Device transport started: pid {process.pid}', flush=True)
            try:
                while process.poll() is None and not STOP.wait(1):
                    pass
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
            print(f'Device transport ended: status {process.returncode}', flush=True)
            if time.monotonic() - started > 30:
                retry_delay = 3
            STOP.wait(retry_delay)
            retry_delay = min(60, retry_delay * 2)


if __name__ == '__main__':
    main()
