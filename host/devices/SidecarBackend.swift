import Foundation
import CryptoKit

/// Private API adapter for the isolated Sidecar helper. Native IPC remains
/// outside the desktop process even after the helper is bundled into Ashacky.
final class SidecarBackend {
    enum Failure: Error { case unavailable, invalidDevice }
    private let framework: UnsafeMutableRawPointer
    private let manager: NSObject

    init() throws {
        guard let framework = dlopen("/System/Library/PrivateFrameworks/SidecarCore.framework/SidecarCore", RTLD_LAZY),
              let type = NSClassFromString("SidecarDisplayManager") as? NSObject.Type,
              type.responds(to: NSSelectorFromString("sharedManager")),
              let manager = type.perform(NSSelectorFromString("sharedManager"))?.takeUnretainedValue() as? NSObject,
              ["devices", "connectedDevices", "connectToDevice:completion:", "disconnectFromDevice:completion:"]
                .allSatisfy({ manager.responds(to: NSSelectorFromString($0)) }) else { throw Failure.unavailable }
        self.framework = framework // Retain the framework for every object and callback.
        self.manager = manager
    }

    private func object(_ target: NSObject, _ name: String) -> AnyObject? {
        let selector = NSSelectorFromString(name)
        guard target.responds(to: selector) else { return nil }
        return target.perform(selector)?.takeUnretainedValue()
    }

    private func devices(_ key: String) throws -> [NSObject] {
        guard let devices = object(manager, key) as? [NSObject], devices.count <= 64 else { throw Failure.unavailable }
        return devices
    }

    private func identifier(_ device: NSObject) throws -> String {
        let value = object(device, "identifier")
        let text: String
        if let uuid = value as? UUID { text = uuid.uuidString.lowercased() }
        else if let string = value as? String, !string.isEmpty, string.utf8.count <= 512 { text = string }
        else { throw Failure.invalidDevice }
        // Never expose Apple's raw device/account identifier as the API target.
        return "sidecar:" + SHA256.hash(data: Data(("ashacky-sidecar\0" + text).utf8))
            .map { String(format: "%02x", $0) }.joined()
    }

    func snapshot() throws -> [[String: Any]] {
        precondition(Thread.isMainThread)
        let connected = try devices("connectedDevices")
        let connectedIDs = Set(try connected.map(identifier))
        var result: [String: [String: Any]] = [:]
        for device in try devices("devices") + connected {
            let id = try identifier(device)
            let name = (object(device, "name") as? String) ?? "iPad"
            result[id] = ["id": id, "name": String(name.prefix(128)), "backend": "sidecar",
                          "connected": connectedIDs.contains(id)]
        }
        guard result.count <= 16 else { throw Failure.unavailable }
        return result.keys.sorted().compactMap { result[$0] }
    }

    func perform(_ action: String, id: String, completion: @escaping (NSError?) -> Void) throws {
        precondition(Thread.isMainThread)
        guard action == "connect" || action == "disconnect" else { throw Failure.invalidDevice }
        let candidates = try devices("devices") + devices("connectedDevices")
        guard let device = try candidates.first(where: { try identifier($0) == id }) else { throw Failure.invalidDevice }
        let callback: @convention(block) (NSError?) -> Void = { error in
            // Native completions may arrive on an arbitrary queue.
            DispatchQueue.main.async { completion(error) }
        }
        let selector = NSSelectorFromString(action == "connect" ? "connectToDevice:completion:" : "disconnectFromDevice:completion:")
        _ = manager.perform(selector, with: device, with: callback)
    }
}
