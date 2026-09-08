"""Shared VM identity and private paths for either macOS account.

LINUXHOST_SESSION_CONFIG selects an explicit JSON configuration. Configuration is mandatory. Secrets are never stored in this file.
"""
import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent.parent


def settings():
    values = dict.fromkeys(('uuid', 'privateDirectory', 'managementSubnet', 'fallbackAddress'))
    configured = os.environ.get('LINUXHOST_SESSION_CONFIG')
    if not configured:
        raise ValueError('LINUXHOST_SESSION_CONFIG must select this installation')
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError('Session configuration path must be absolute')
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid())
                or info.st_mode & 0o022):
            raise ValueError('Session configuration must be a protected regular file')
        supplied = json.loads(path.read_text())
        if not isinstance(supplied, dict) or set(supplied) != set(values):
            raise ValueError('Session configuration must specify all four known fields')
        values = supplied
    values['uuid'] = str(uuid.UUID(values['uuid']))
    private = Path(values['privateDirectory'])
    if not private.is_absolute():
        raise ValueError('Private directory must be absolute')
    info = private.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise ValueError('Private directory must belong exclusively to the current user')
    values['privateDirectory'] = private
    values['managementSubnet'] = ipaddress.IPv4Network(values['managementSubnet'])
    fallback = ipaddress.IPv4Address(values['fallbackAddress'])
    if fallback not in values['managementSubnet']:
        raise ValueError('Fallback must belong to the management subnet')
    return values


def address(config):
    # The background transport must never launch an application while retrying.
    # Probe vmnet lease candidates; SSH still pins the key and exact DMI identity.
    import re
    fallback = str(config['fallbackAddress'])
    try:
        leases = Path('/var/db/dhcpd_leases').read_text()
        candidates = []
        for value in re.findall(r'^\s*ip_address=([^\s]+)', leases, re.MULTILINE):
            candidate = ipaddress.IPv4Address(value)
            if candidate in config['managementSubnet'] and str(candidate) not in candidates:
                candidates.append(str(candidate))
        if fallback in candidates:
            return fallback
        if len(candidates) == 1:
            return candidates[0]
    except (OSError, ValueError):
        pass
    return fallback
