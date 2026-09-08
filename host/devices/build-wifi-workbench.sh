#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p build/host/WiFiWorkbench.app/Contents/MacOS
swiftc -swift-version 5 -O host/common/IPC.swift host/devices/WiFiBackend.swift host/devices/WPA2Key.swift host/devices/WiFiWorkbench.swift \
    -o build/host/WiFiWorkbench.app/Contents/MacOS/WiFiWorkbench \
    -framework AppKit -framework CoreWLAN -framework CoreLocation -framework SystemConfiguration
cat > build/host/WiFiWorkbench.app/Contents/Info.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.linuxhost.wifi-workbench</string>
<key>CFBundleName</key><string>LinuxHost Wi-Fi</string>
<key>CFBundleExecutable</key><string>WiFiWorkbench</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>NSLocationUsageDescription</key><string>Test real Wi-Fi scans and connection management for the Linux virtual machine.</string>
<key>NSLocationWhenInUseUsageDescription</key><string>Test real Wi-Fi scans and connection management for the Linux virtual machine.</string>
</dict></plist>
EOF
cat > build/host/wifi-workbench-entitlements.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict><key>com.apple.security.personal-information.location</key><true/></dict></plist>
EOF
codesign --force --sign - --options runtime --entitlements build/host/wifi-workbench-entitlements.plist build/host/WiFiWorkbench.app
