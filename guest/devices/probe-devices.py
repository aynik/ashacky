#!/usr/bin/env python3
"""Load virtual devices only for the VM identity selected at provisioning."""
import json
from pathlib import Path
import re
import subprocess
import uuid


def validate(config, dmi_uuid, lower_mac):
    if uuid.UUID(dmi_uuid.strip()) != uuid.UUID(config['vmUUID']):
        raise ValueError('VM identity does not match the integration configuration')
    interface = config['wifiInterface']
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', interface):
        raise ValueError('Invalid Wi-Fi transport interface')
    if lower_mac.strip().lower() != config['wifiMAC'].lower():
        raise ValueError('Wi-Fi transport NIC identity does not match')
    number = config.get('cameraVideoNumber', 10)
    if type(number) is not int or not 0 <= number <= 63:
        raise ValueError('Invalid camera device number')
    return interface, number


def main():
    config = json.loads(Path('/etc/ashacky/devices.json').read_text())
    interface = config['wifiInterface']
    # Validate the name before using it as a path component.
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', interface):
        raise ValueError('Invalid interface name')
    interface, number = validate(config,
        Path('/sys/class/dmi/id/product_uuid').read_text(),
        (Path('/sys/class/net') / interface / 'address').read_text())
    for command in [
        ['ip', 'link', 'set', interface, 'up'],
        ['modprobe', 'cfg80211'],
        ['modprobe', 'linuxhost_wifi', 'lowerdev=' + interface],
        ['modprobe', 'v4l2loopback', 'video_nr=' + str(number),
         'card_label=LinuxHost-Camera', 'exclusive_caps=1'],
    ]:
        subprocess.run(command, check=True, timeout=20)


if __name__ == '__main__':
    main()
