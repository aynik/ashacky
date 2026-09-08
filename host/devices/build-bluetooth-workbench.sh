#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
app=build/host/BluetoothWorkbench.app
mkdir -p "$app/Contents/MacOS"
swiftc -parse-as-library -swift-version 5 -O host/common/IPC.swift host/devices/AudioBackend.swift host/devices/BluetoothWorkbench.swift -o "$app/Contents/MacOS/BluetoothWorkbench" -framework AppKit -framework CoreBluetooth -framework IOBluetooth -framework SystemConfiguration -framework CoreAudio
python3 - <<'PY'
import plistlib
from pathlib import Path
p=Path('build/host/BluetoothWorkbench.app/Contents/Info.plist')
p.write_bytes(plistlib.dumps(dict(CFBundleIdentifier='local.linuxhost.bluetooth-workbench', CFBundleName='LinuxHost Bluetooth', CFBundleExecutable='BluetoothWorkbench', CFBundlePackageType='APPL', NSBluetoothAlwaysUsageDescription='Discover nearby Bluetooth devices and pair the device you explicitly select for Linux virtual-machine integration testing.', NSBluetoothPeripheralUsageDescription='Discover nearby Bluetooth devices for the local Linux integration test.')))
Path('build/host/bluetooth-entitlements.plist').write_bytes(plistlib.dumps({'com.apple.security.device.bluetooth':True}))
PY
codesign --force --sign - --options runtime --entitlements build/host/bluetooth-entitlements.plist "$app"
codesign --verify --strict "$app"
