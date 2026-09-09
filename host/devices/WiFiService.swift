import CoreLocation
import CoreWLAN
import SystemConfiguration

/// Main-queue invalidations. No SSID/BSSID or signal value enters public status.
/// CoreWLAN event registration works with the reference ad-hoc signature. Do
/// not add its restricted com.apple.wifi.events entitlement to that signature.
final class WiFiObserver: NSObject, CWEventDelegate {
    private let client = CWWiFiClient.shared()
    private let epoch = UUID().uuidString
    private var generation: UInt64 = 0
    private var registered = false
    private var available = false
    private var pending: DispatchWorkItem?
    var changed: (() -> Void)?
    var revision: String? { available ? "\(epoch):\(generation)" : nil }

    func start() throws {
        client.delegate = self
        do {
            for event: CWEventType in [.powerDidChange, .ssidDidChange, .bssidDidChange,
                                       .linkDidChange, .linkQualityDidChange] {
                try client.startMonitoringEvent(with: event)
            }
            registered = true; available = true
        } catch { stop(); throw error }
    }
    func invalidate() {
        guard registered, pending == nil else { return }
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.registered else { return }
            self.pending = nil; self.generation &+= 1; self.changed?()
        }
        pending = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05, execute: work)
    }
    private func event() {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.registered else { return }
            self.available = true; self.invalidate()
        }
    }
    func powerStateDidChangeForWiFiInterface(withName: String) { event() }
    func ssidDidChangeForWiFiInterface(withName: String) { event() }
    func bssidDidChangeForWiFiInterface(withName: String) { event() }
    func linkDidChangeForWiFiInterface(withName: String) { event() }
    func linkQualityDidChangeForWiFiInterface(withName: String, rssi: Int, transmitRate: Double) { event() }
    func clientConnectionInterrupted() {
        // CoreWLAN rearms registrations after interruption. Until another
        // callback arrives, do not advertise healthy notification delivery.
        DispatchQueue.main.async { [weak self] in
            guard let self, self.registered else { return }
            self.available = false; self.invalidate()
        }
    }
    func clientConnectionInvalidated() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.stop(); self.changed?()
        }
    }
    func stop() {
        registered = false; available = false
        pending?.cancel(); pending = nil
        try? client.stopMonitoringAllEvents()
        client.delegate = nil
    }
}

/// CoreWLAN RPC endpoint owned by the Ashacky application.
final class WiFiService: NSObject, CLLocationManagerDelegate {
    let manager = CLLocationManager()
    let backend = WiFiBackend()
    let queue = DispatchQueue(label: "local.ashacky.wifi")
    var processLock: ServiceProcessLock?
    var server: UnixServer?
    var authorizationChanged: (() -> Void)?
    let observer = WiFiObserver()

    func start(directory: URL) throws {
        processLock = try ServiceProcessLock(path: directory.appendingPathComponent("wifi-workbench.lock").path)
        server = try UnixServer(path: directory.appendingPathComponent("wifi-workbench.sock").path) { [self] fd, request in
            var uid: uid_t = 0; var gid: gid_t = 0
            guard getpeereid(fd, &uid, &gid) == 0, uid == getuid() else {
                return ["ok": false, "error": "Unauthorized peer"]
            }
            let reply = queue.sync {
                backend.perform(request) {
                    var console: uid_t = 0
                    _ = SCDynamicStoreCopyConsoleUser(nil, &console, nil)
                    return console == getuid()
                }
            }
            if ["wifi-connect", "wifi-disconnect", "wifi-power"].contains(request["action"] as? String ?? "") {
                // An operation may have changed state even if its reply failed.
                DispatchQueue.main.async { self.observer.invalidate() }
            }
            return reply
        }
        manager.delegate = self
        do { try observer.start() }
        catch { NSLog("Wi-Fi notifications unavailable; using compatibility refresh (code %ld)", (error as NSError).code) }
    }
    func requestAuthorization() {
        guard AshackyHostServices.active() else { return }
        manager.requestWhenInUseAuthorization()
    }
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        observer.invalidate(); authorizationChanged?()
    }
}
