#!/usr/bin/env python3
"""Maintain the private host-control Unix socket forward to this guest."""
import json, os, pathlib, signal, subprocess, time
stop = False
signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__('stop', True))
config = json.loads(pathlib.Path(os.environ['LINUXHOST_CONTROL_CONFIG']).read_text())
sock = config['socket']
ssh = pathlib.Path(__file__).resolve().parents[1] / 'transport/probe-ssh'
while not stop:
    if not os.path.exists(sock):
        time.sleep(2)
        continue
    process = subprocess.Popen(['/bin/bash', str(ssh), '-N', '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=3',
        '-R', '/run/linuxhost-host.sock:' + sock])
    try:
        while process.poll() is None and not stop:
            time.sleep(1)
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    if not stop: time.sleep(3)
