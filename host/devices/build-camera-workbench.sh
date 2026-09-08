#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
app=build/host/CameraWorkbench.app
mkdir -p "$app/Contents/MacOS"
swiftc -swift-version 5 -O host/common/IPC.swift host/devices/CameraWorkbench.swift -o "$app/Contents/MacOS/CameraWorkbench" -framework AppKit -framework AVFoundation -framework SystemConfiguration
cat > "$app/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.linuxhost.camera-workbench</string>
<key>CFBundleName</key><string>LinuxHost Camera</string>
<key>CFBundleExecutable</key><string>CameraWorkbench</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>NSCameraUsageDescription</key><string>Send a short live camera stream to the local Linux virtual machine. No recording is saved.</string>
</dict></plist>
EOF
cat > build/host/camera-entitlements.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict><key>com.apple.security.device.camera</key><true/></dict></plist>
EOF
codesign --force --sign - --options runtime --entitlements build/host/camera-entitlements.plist "$app"
codesign --verify --strict "$app"
