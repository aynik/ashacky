#!/usr/bin/python3
"""SSH to the configured VM only after checking its pinned key and DMI identity."""
import os
import subprocess
import sys
from vm_session import address, settings


def main():
    config = settings()
    private = config['privateDirectory']
    options = ['-i', str(private / 'id_ed25519'),
               '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile="' + str(private / 'known_hosts').replace('\\', '\\\\').replace('"', '\\"') + '"',
               '-o', 'HostKeyAlias=linuxhost-vm', '-o', 'ConnectTimeout=5',
               '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=3']
    destination = 'root@' + address(config)
    result = subprocess.run(['/usr/bin/ssh', '-n', *options, destination,
                             'cat /sys/class/dmi/id/product_uuid'],
                            capture_output=True, text=True, timeout=25)
    if result.returncode != 0:
        raise RuntimeError('Guest identity connection failed: ' + result.stderr.strip()[:1500])
    if result.stdout.strip().lower() != config['uuid']:
        raise RuntimeError('Refusing: address does not belong to the configured VM')
    os.execv('/usr/bin/ssh', ['ssh', *options, destination, *sys.argv[1:]])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'VM SSH: {error}', file=sys.stderr)
        sys.exit(1)
