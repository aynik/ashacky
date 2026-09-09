import AppKit

/// Session services share Ashacky's main run loop and macOS application identity.
@objc(AshackySessionServices) public final class AshackySessionServices: NSObject {
    private var channel: ControlChannel?
    @objc public var controlWrite: ((Data, @escaping (Bool) -> Void) -> Void)?
    @objc public var controlFailed: (() -> Void)?

    @objc public func controlOpened() { channel?.close() }
    @objc public func controlClosed() { channel?.close() }
    @objc public func controlReceived(_ data: Data) { channel?.receive(data) }
    @objc public var statusChanged: (() -> Void)?

    /// Read-only telemetry; power/authentication requests still use authenticated RPC.
    @objc public func statusMessage(audioRevision: String?, bluetoothRevision: String?, wifiRevision: String?) -> Data? {
        var status = Control.status()
        // Only an opaque invalidation token travels in the public telemetry.
        // Device identities and selection keep the existing authorized RPC path.
        if let audioRevision { status["audioRevision"] = audioRevision }
        if let bluetoothRevision { status["bluetoothRevision"] = bluetoothRevision }
        if let wifiRevision { status["wifiRevision"] = wifiRevision }
        var data = try? JSONSerialization.data(withJSONObject: ["version": 1, "status": status])
        data?.append(10)
        return data
    }

    @objc public func start() throws {
        try Control.start()
        Control.statusChanged = { [weak self] in self?.statusChanged?() }
        let control = ControlChannel(token: Control.config["token"] as! String)
        channel = control
        control.write = { [weak self] data, completion in
            guard let write = self?.controlWrite else { completion(false); return }
            write(data, completion)
        }
        control.failed = { [weak self] in self?.controlFailed?() }
        let directory = URL(fileURLWithPath: Control.config["socket"] as! String).deletingLastPathComponent().deletingLastPathComponent()
        let endpoints = ["host": Control.config["socket"] as! String,
                         "wifi": directory.appendingPathComponent("wifi-workbench.sock").path,
                         "bluetooth": directory.appendingPathComponent("bluetooth-workbench.sock").path]
        control.request = { service, payload, completion in
            guard let endpoint = endpoints[service] else { completion(["ok": false]); return }
            // Existing listeners retain their token, active-console and request
            // validation. Never call their blocking adapters on the main queue.
            DispatchQueue.global(qos: .userInitiated).async {
                let result = (try? requestSocket(endpoint, payload)) ?? ["ok": false, "error": "Host service unavailable"]
                completion(result)
            }
        }

    }

    @objc public func stop() {
        channel?.close()
        channel = nil
        Control.stop()
    }

}
