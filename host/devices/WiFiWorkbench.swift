import AppKit
import CoreLocation
import SystemConfiguration

/// Isolated development harness. Uses the same backend as LinuxHost, with a
/// private local socket and the logged-in developer's explicit TCC permission.
final class WiFiWorkbench: NSObject, NSApplicationDelegate, CLLocationManagerDelegate {
    lazy var manager = CLLocationManager()
    let backend = WiFiBackend()
    let queue = DispatchQueue(label: "local.linuxhost.wifi-workbench")
    var processLock: WorkbenchProcessLock?
    var server: UnixServer?
    var window: NSWindow?
    let networks = NSPopUpButton()
    let password = NSSecureTextField()
    let derivedKey = NSButton(checkboxWithTitle: "Use derived WPA2 key (Linux compatibility test)", target: nil, action: nil)
    let status = NSTextField(wrappingLabelWithString: "Scan, select your network, then enter its password locally.")
    var entries: [[String: Any]] = []
    var controls: [NSControl] = []

    static func active() -> Bool {
        var console: uid_t = 0
        _ = SCDynamicStoreCopyConsoleUser(nil, &console, nil)
        return console == getuid()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let serviceMode = CommandLine.arguments.contains("--service")
        let path = CommandLine.arguments.dropFirst().first(where: { !$0.hasPrefix("--") }) ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Ashacky/devices").path
        let directory = URL(fileURLWithPath: path)
        do {
            var info = stat()
            guard lstat(directory.path, &info) == 0, info.st_uid == getuid(),
                  info.st_mode & 0o077 == 0, info.st_mode & S_IFMT == S_IFDIR else {
                throw NSError(domain: "Workbench", code: 1)
            }
            processLock = try WorkbenchProcessLock(path: directory.appendingPathComponent("wifi-workbench.lock").path)
            server = try UnixServer(path: directory.appendingPathComponent("wifi-workbench.sock").path) { [self] fd, request in
                var uid: uid_t = 0; var gid: gid_t = 0
                guard getpeereid(fd, &uid, &gid) == 0, uid == getuid() else {
                    return ["ok": false, "error": "Unauthorized peer"]
                }
                return queue.sync {
                    backend.perform(request) {
                        var console: uid_t = 0
                        _ = SCDynamicStoreCopyConsoleUser(nil, &console, nil)
                        return console == getuid()
                    }
                }
            }
        } catch { fputs("Wi-Fi workbench could not start\n", stderr); NSApp.terminate(nil); return }
        if serviceMode {
            manager.delegate = self
            if Self.active(), manager.authorizationStatus == .notDetermined {
                manager.requestWhenInUseAuthorization()
            }
            return // Only the native permission prompt; never open the test form.
        }
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 350),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        win.title = "LinuxHost Wi-Fi test"
        let label = NSTextField(wrappingLabelWithString: "Test the Mac's built-in Wi-Fi. Passwords are used only for the requested connection and are not saved by this app. A connection attempt may disconnect Wi-Fi; keep Ethernet connected.")
        label.frame = NSRect(x: 24, y: 264, width: 492, height: 64)
        win.contentView?.addSubview(label)
        let button = NSButton(title: "Request Wi-Fi permission", target: self, action: #selector(requestAuthorization))
        button.frame = NSRect(x: 24, y: 218, width: 240, height: 32)
        win.contentView?.addSubview(button)
        let scan = NSButton(title: "Scan networks", target: self, action: #selector(scanNetworks))
        scan.frame = NSRect(x: 290, y: 218, width: 226, height: 32)
        networks.frame = NSRect(x: 24, y: 174, width: 492, height: 32)
        networks.setAccessibilityLabel("Wi-Fi network")
        password.frame = NSRect(x: 24, y: 128, width: 340, height: 28)
        password.placeholderString = "Wi-Fi password or key"
        password.setAccessibilityLabel("Wi-Fi password or key")
        let connect = NSButton(title: "Connect", target: self, action: #selector(connectNetwork))
        connect.frame = NSRect(x: 378, y: 126, width: 138, height: 32)
        derivedKey.frame = NSRect(x: 24, y: 94, width: 492, height: 24)
        status.frame = NSRect(x: 24, y: 16, width: 492, height: 64)
        for view in [scan, networks, password, connect, derivedKey, status] { win.contentView?.addSubview(view) }
        controls = [scan, networks, password, connect, derivedKey]
        win.center(); win.makeKeyAndOrderFront(nil); window = win
        NSApp.activate(ignoringOtherApps: true)
        manager.delegate = self
    }
    @objc func requestAuthorization() {
        manager.requestWhenInUseAuthorization()
    }
    func busy(_ value: Bool) { controls.forEach { $0.isEnabled = !value } }
    @objc func scanNetworks() {
        busy(true); password.stringValue = ""; status.stringValue = "Scanning…"
        queue.async { [self] in
            var page = backend.perform(["action": "wifi-scan"], isActive: Self.active)
            var found: [[String: Any]] = []
            while page["ok"] as? Bool == true {
                found += page["networks"] as? [[String: Any]] ?? []
                guard let offset = page["nextOffset"], let id = page["scanID"] else { break }
                page = backend.perform(["action": "wifi-scan-page", "scanID": id, "offset": offset], isActive: Self.active)
            }
            let success = page["ok"] as? Bool == true
            DispatchQueue.main.async { [self] in
                entries = success ? found : []; networks.removeAllItems()
                for (index, entry) in entries.enumerated() {
                    let data = Data(base64Encoded: entry["ssidBase64"] as? String ?? "") ?? Data()
                    let name = String(data: data, encoding: .utf8) ?? "Non-UTF8 SSID (\(data.base64EncodedString()))"
                    let band = entry["channelBand"] as? Int == 1 ? "2.4 GHz" : "5/6 GHz"
                    networks.addItem(withTitle: "\(index + 1). \(name) — \(band), \(entry["signalDBm"] ?? "?") dBm")
                }
                status.stringValue = success ? "Select a network and connect within 60 seconds. Secured networks require a password or key." : page["error"] as? String ?? "Scan failed"
                busy(false)
            }
        }
    }
    @objc func connectNetwork() {
        let index = networks.indexOfSelectedItem
        guard entries.indices.contains(index), let id = entries[index]["networkID"] as? String else {
            status.stringValue = "Scan and select a network first."; return
        }
        let expectedSSID = entries[index]["ssidBase64"] as? String
        var request: [String: Any] = ["action": "wifi-connect", "networkID": id]
        let testingKey = derivedKey.state == .on
        if testingKey {
            do {
                guard let encoded = expectedSSID, let ssid = Data(base64Encoded: encoded) else { throw WPA2Key.Failure.invalidSSID }
                request["password"] = try WPA2Key.derive(passphrase: password.stringValue, ssid: ssid)
            } catch {
                password.stringValue = ""
                status.stringValue = "This WPA2 test needs an 8–63 character ASCII passphrase and a named network."
                return
            }
        } else if !password.stringValue.isEmpty { request["password"] = password.stringValue }
        password.stringValue = ""; busy(true); status.stringValue = "Connecting…"
        queue.async { [self, request] in
            let result = backend.perform(request, isActive: Self.active)
            let state = backend.perform(["action": "wifi-status"], isActive: Self.active)
            let connected = result["ok"] as? Bool == true && state["ssidBase64"] as? String == expectedSSID
            DispatchQueue.main.async { [self] in
                status.stringValue = connected ? (testingKey ? "Connected using the derived WPA2 key. Linux credential compatibility test passed." : "Connected. macOS confirms the selected network and reports signal strength.") : result["error"] as? String ?? "Association was not confirmed. Scan again before retrying."
                busy(false)
            }
        }
    }
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        fputs("Location authorization: \(manager.authorizationStatus.rawValue)\n", stderr)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { !CommandLine.arguments.contains("--service") }
}

@main enum WorkbenchMain {
    static func main() {
        signal(SIGPIPE, SIG_IGN)
        let app = NSApplication.shared
        let delegate = WiFiWorkbench()
        app.setActivationPolicy(CommandLine.arguments.contains("--service") ? .accessory : .regular); app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
