import CoreLocation
import SystemConfiguration

/// CoreWLAN RPC endpoint owned by the Ashacky application.
final class WiFiService: NSObject, CLLocationManagerDelegate {
    let manager = CLLocationManager()
    let backend = WiFiBackend()
    let queue = DispatchQueue(label: "local.ashacky.wifi")
    var processLock: ServiceProcessLock?
    var server: UnixServer?
    var authorizationChanged: (() -> Void)?

    func start(directory: URL) throws {
        processLock = try ServiceProcessLock(path: directory.appendingPathComponent("wifi-workbench.lock").path)
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
        manager.delegate = self
    }
    func requestAuthorization() {
        guard AshackyHostServices.active() else { return }
        manager.requestWhenInUseAuthorization()
    }
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) { authorizationChanged?() }
}
