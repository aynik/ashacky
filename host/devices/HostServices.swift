import AppKit
import AVFoundation
import CoreBluetooth
import CoreLocation
import SystemConfiguration

/// Linked into the CocoaSpice executable: one process and one TCC identity.
@objc(AshackyHostServices) public final class AshackyHostServices: NSObject {
    let wifi = WiFiService()
    let bluetooth = BluetoothService()
    let camera = CameraService()
    var processLock: ServiceProcessLock?
    var server: UnixServer?
    var window: NSWindow?
    var labels: [NSTextField] = []
    var buttons: [NSButton] = []
    var timer: Timer?
    var launcherMonitor: DispatchSourceProcess?

    static func active() -> Bool {
        var uid: uid_t = 0
        _ = SCDynamicStoreCopyConsoleUser(nil, &uid, nil)
        return uid == getuid()
    }

    @objc(startAtDirectory:error:) public func start(directory: String) throws {
        let url = URL(fileURLWithPath: directory)
        var info = stat()
        guard lstat(directory, &info) == 0, info.st_uid == getuid(),
              info.st_mode & 0o077 == 0, info.st_mode & S_IFMT == S_IFDIR else {
            throw IPCError.message("Ashacky needs an existing private directory owned by this user (mode 0700)")
        }
        processLock = try ServiceProcessLock(path: url.appendingPathComponent("host-services.lock").path)
        try wifi.start(directory: url)
        try bluetooth.start(directory: url)
        try camera.start(directory: url)
        // Read authorization without opening a camera stream or scanning devices.
        server = try UnixServer(path: url.appendingPathComponent("host-services.sock").path) { [self] fd, request in
            var uid: uid_t = 0, gid: gid_t = 0
            guard getpeereid(fd, &uid, &gid) == 0, uid == getuid() else { return ["ok": false, "error": "Unauthorized peer"] }
            guard request.count == 1, request["action"] as? String == "status" else {
                return ["ok": false, "error": "Unsupported operation"]
            }
            return DispatchQueue.main.sync {
                ["ok": true, "active": Self.active(), "permissions": self.permissions(),
                 "bundleID": Bundle.main.bundleIdentifier ?? ""]
            }
        }
        // LaunchServices owns the app process. Exit if its supervisor-side
        // launcher disappears, including when startup completes after cancellation.
        if let raw = ProcessInfo.processInfo.environment["ASHACKY_LAUNCHER_PID"], let pid = Int32(raw) {
            guard pid > 1, kill(pid, 0) == 0 else { throw IPCError.message("Launcher has exited") }
            let monitor = DispatchSource.makeProcessSource(identifier: pid, eventMask: .exit, queue: .main)
            monitor.setEventHandler { NSApp.terminate(nil) }
            monitor.resume(); launcherMonitor = monitor
            guard kill(pid, 0) == 0 else { throw IPCError.message("Launcher has exited") }
        }
        NSLog("Ashacky host services ready")
    }

    func permissions() -> [String: String] {
        func av(_ media: AVMediaType) -> String {
            switch AVCaptureDevice.authorizationStatus(for: media) {
            case .authorized: return "allowed"
            case .notDetermined: return "undecided"
            default: return "denied"
            }
        }
        let location: String
        switch wifi.manager.authorizationStatus {
        case .authorizedAlways, .authorizedWhenInUse: location = CLLocationManager.locationServicesEnabled() ? "allowed" : "disabled"
        case .notDetermined: location = "undecided"
        default: location = "denied"
        }
        let bt: String
        switch CBManager.authorization {
        case .allowedAlways: bt = "allowed"
        case .notDetermined: bt = "undecided"
        default: bt = "denied"
        }
        return ["location": location, "bluetooth": bt, "camera": av(.video), "microphone": av(.audio)]
    }

    @objc public func showPermissions() {
        if let window { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 335),
                           styleMask: [.titled, .closable], backing: .buffered, defer: false)
        win.title = "Ashacky — Permissions"
        win.isReleasedWhenClosed = false
        let text = NSTextField(wrappingLabelWithString: "Allow Ashacky to provide these devices to Linux. Location access is required by macOS for Wi-Fi network discovery. Camera and microphone access is used only when Linux requests it.")
        text.frame = NSRect(x: 24, y: 248, width: 492, height: 64)
        win.contentView?.addSubview(text)
        for (index, title) in ["Wi-Fi (Location)", "Bluetooth", "Camera", "Microphone"].enumerated() {
            let label = NSTextField(labelWithString: title)
            label.frame = NSRect(x: 24, y: 197 - index * 48, width: 330, height: 24)
            labels.append(label); win.contentView?.addSubview(label)
            let button = NSButton(title: "Allow", target: self, action: #selector(requestPermission(_:)))
            button.tag = index
            button.frame = NSRect(x: 372, y: 192 - index * 48, width: 144, height: 32)
            buttons.append(button); win.contentView?.addSubview(button)
        }
        window = win
        refreshPermissions()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.refreshPermissions() }
        win.center(); win.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }

    func refreshPermissions() {
        let state = permissions()
        for (index, key) in ["location", "bluetooth", "camera", "microphone"].enumerated() {
            let allowed = state[key] == "allowed"
            let title = ["Wi-Fi (Location)", "Bluetooth", "Camera", "Microphone"][index]
            labels[index].stringValue = title + " — " + (state[key] ?? "unknown").capitalized
            buttons[index].isEnabled = !allowed && Self.active()
            buttons[index].title = allowed ? "Allowed" : state[key] == "undecided" ? "Allow" : "Open Settings"
        }
        bluetooth.prepareCentral()
    }

    @objc func requestPermission(_ sender: NSButton) {
        guard Self.active() else { return }
        let keys = ["location", "bluetooth", "camera", "microphone"]
        guard keys.indices.contains(sender.tag) else { return }
        if permissions()[keys[sender.tag]] != "undecided" {
            let panels = ["Privacy_LocationServices", "Privacy_Bluetooth", "Privacy_Camera", "Privacy_Microphone"]
            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?" + panels[sender.tag]) {
                NSWorkspace.shared.open(url)
            }
            return
        }
        switch sender.tag {
        case 0: wifi.requestAuthorization()
        case 1: bluetooth.prepareCentral(requestPermission: true)
        case 2: camera.requestAuthorization()
        case 3: AVCaptureDevice.requestAccess(for: .audio) { _ in }
        default: break
        }
    }

    @objc public func stop() {
        timer?.invalidate()
        bluetooth.stop()
        camera.shutdown()
        if let path = ProcessInfo.processInfo.environment["ASHACKY_EXIT_RECEIPT"] {
            try? Data("clean\n".utf8).write(to: URL(fileURLWithPath: path), options: .atomic)
        }
    }
}
