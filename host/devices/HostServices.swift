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
    var permissionFlowStarted = false
    var pendingPermission: String?
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
        wifi.authorizationChanged = { [weak self] in self?.advancePermissions() }
        bluetooth.authorizationChanged = { [weak self] in self?.advancePermissions() }
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
        if !CLLocationManager.locationServicesEnabled() {
            location = "disabled"
        } else {
            switch wifi.manager.authorizationStatus {
            case .authorizedAlways, .authorizedWhenInUse: location = "allowed"
            case .notDetermined: location = "undecided"
            default: location = "denied"
            }
        }
        let bt: String
        switch CBManager.authorization {
        case .allowedAlways: bt = "allowed"
        case .notDetermined: bt = "undecided"
        default: bt = "denied"
        }
        return ["location": location, "bluetooth": bt, "camera": av(.video), "microphone": av(.audio)]
    }

    /// Use the system's own prompts, one at a time. Previously denied access
    /// remains under the user's control in System Settings; it is not re-prompted.
    @objc public func requestMissingPermissions() {
        permissionFlowStarted = true
        advancePermissions()
    }

    func advancePermissions() {
        guard permissionFlowStarted, Self.active() else { return }
        let state = permissions()
        if let pendingPermission, state[pendingPermission] == "undecided" { return }
        pendingPermission = nil
        guard let key = ["location", "bluetooth", "camera", "microphone"].first(where: { state[$0] == "undecided" }) else {
            permissionFlowStarted = false
            NSLog("Ashacky permissions: %@", state)
            return
        }
        pendingPermission = key
        switch key {
        case "location": wifi.requestAuthorization()
        case "bluetooth": bluetooth.prepareCentral(requestPermission: true)
        default:
            AVCaptureDevice.requestAccess(for: key == "camera" ? .video : .audio) { [weak self] _ in
                DispatchQueue.main.async { self?.advancePermissions() }
            }
        }
    }

    @objc public func stop() {
        bluetooth.stop()
        camera.shutdown()
        if let path = ProcessInfo.processInfo.environment["ASHACKY_EXIT_RECEIPT"] {
            try? Data("clean\n".utf8).write(to: URL(fileURLWithPath: path), options: .atomic)
        }
    }
}
