import Foundation
import CoreWLAN
import CoreLocation

/// Host-side operations for a future cfg80211 frontend. Never logs credentials
/// or network identities. Access policy is enforced again immediately before an
/// operation that can change connectivity.
final class WiFiBackend {
    private let client = CWWiFiClient.shared()
    private struct Snapshot {
        let created: TimeInterval
        let networks: [String: CWNetwork]
        let entries: [[String: Any]]
        let truncated: Bool
    }
    private var snapshots: [String: Snapshot] = [:]
    private var now: TimeInterval { ProcessInfo.processInfo.systemUptime }

    private func expireSnapshots() {
        let time = now
        snapshots = snapshots.filter { time - $0.value.created < 60 }
    }

    enum Failure: Error { case invalid, unavailable, permission, stale, inactive, unsupported, credentials }

    static func validate(_ request: [String: Any]) throws {
        switch request["action"] as? String {
        case "wifi-status", "wifi-scan", "wifi-disconnect": break
        case "wifi-scan-page":
            guard let id = request["scanID"] as? String, UUID(uuidString: id) != nil,
                  let offset = request["offset"] as? NSNumber,
                  CFGetTypeID(offset) != CFBooleanGetTypeID(),
                  offset.doubleValue == Double(offset.intValue),
                  (0...256).contains(offset.intValue) else { throw Failure.invalid }
        case "wifi-power":
            guard let value = request["enabled"] as? NSNumber,
                  CFGetTypeID(value) == CFBooleanGetTypeID() else { throw Failure.invalid }
        case "wifi-connect":
            guard let id = request["networkID"] as? String, UUID(uuidString: id) != nil else { throw Failure.invalid }
            if let password = request["password"] {
                guard let text = password as? String, text.utf8.count <= 256,
                      !text.contains("\0") else { throw Failure.invalid }
            }
        default: throw Failure.invalid
        }
    }

    // Called on a dedicated serial queue; the cache never crosses queues.
    func perform(_ request: [String: Any], isActive: () -> Bool) -> [String: Any] {
        do {
            try Self.validate(request)
            guard isActive() else { throw Failure.inactive }
            expireSnapshots()
            guard let interface = client.interface() else { throw Failure.unavailable }
            let action = request["action"] as! String
            if action == "wifi-status" {
                let authorization = CLLocationManager().authorizationStatus
                var state: [String: Any] = ["ok": true, "powered": interface.powerOn(),
                    "interface": interface.interfaceName ?? "", "networkIdentityAvailable": interface.ssidData() != nil,
                    "locationAuthorized": authorization == .authorizedAlways || authorization == .authorized]
                if let ssid = interface.ssidData() {
                    state["ssidBase64"] = ssid.base64EncodedString()
                    state["signalDBm"] = interface.rssiValue()
                    if let bssid = interface.bssid() { state["bssid"] = bssid }
                }
                return state
            }
            if ["wifi-scan", "wifi-scan-page", "wifi-connect"].contains(action) {
                let authorization = CLLocationManager().authorizationStatus
                guard authorization == .authorizedAlways || authorization == .authorized else { throw Failure.permission }
            }
            switch action {
            case "wifi-scan":
                let found = try interface.scanForNetworks(withSSID: nil)
                guard isActive() else { throw Failure.inactive }
                var networks: [String: CWNetwork] = [:]
                var scanEntries: [[String: Any]] = []
                let scanID = UUID().uuidString
                for network in found.sorted(by: { $0.rssiValue > $1.rssiValue }).prefix(256) {
                    guard let ssid = network.ssidData else { continue }
                    let id = UUID().uuidString
                    networks[id] = network
                    let enterprise = network.supportsSecurity(.enterprise)
                    let open = network.supportsSecurity(.none)
                    var entry: [String: Any] = ["networkID": id, "ssidBase64": ssid.base64EncodedString(),
                        "signalDBm": network.rssiValue, "channel": network.wlanChannel?.channelNumber ?? 0,
                        "enterprise": enterprise, "open": open,
                        "personal": network.supportsSecurity(.personal)]
                    if let bssid = network.bssid { entry["bssid"] = bssid }
                    if let channel = network.wlanChannel {
                        entry["channelBand"] = channel.channelBand.rawValue
                    }
                    // Forward actual beacon/probe IEs, including RSN, rather
                    // than fabricating security capabilities in Linux.
                    if let data = network.informationElementData, data.count <= 4096 {
                        entry["informationElementsBase64"] = data.base64EncodedString()
                    }
                    scanEntries.append(entry)
                }
                expireSnapshots()
                // Retain a few independent readers without allowing unbounded
                // CWNetwork/IE retention. A new scan must not invalidate the
                // immediately preceding scan or a network selected in the UI.
                if snapshots.count >= 4,
                   let oldest = snapshots.min(by: { $0.value.created < $1.value.created })?.key {
                    snapshots.removeValue(forKey: oldest)
                }
                let snapshot = Snapshot(created: now, networks: networks,
                    entries: scanEntries, truncated: found.count > 256)
                snapshots[scanID] = snapshot
                return scanPage(id: scanID, snapshot: snapshot, offset: 0)
            case "wifi-scan-page":
                let id = request["scanID"] as! String
                guard let snapshot = snapshots[id] else { throw Failure.stale }
                return scanPage(id: id, snapshot: snapshot, offset: (request["offset"] as! NSNumber).intValue)
            case "wifi-connect":
                let id = request["networkID"] as! String
                guard let network = snapshots.values.compactMap({ $0.networks[id] }).first else { throw Failure.stale }
                // Enterprise credentials/certificates need a separate contract;
                // never accidentally downgrade to personal authentication.
                guard !network.supportsSecurity(.enterprise) else { throw Failure.unsupported }
                // CoreWLAN does not reliably reuse saved credentials. A nil
                // password on this host failed and dropped the association.
                if !network.supportsSecurity(.none) {
                    guard let password = request["password"] as? String,
                          !password.isEmpty else { throw Failure.credentials }
                }
                guard isActive() else { throw Failure.inactive }
                try interface.associate(to: network, password: request["password"] as? String)
            case "wifi-disconnect":
                guard isActive() else { throw Failure.inactive }
                interface.disassociate()
            case "wifi-power":
                guard isActive() else { throw Failure.inactive }
                try interface.setPower(request["enabled"] as! Bool)
            default: throw Failure.invalid
            }
            return ["ok": true]
        } catch let error as Failure {
            let descriptions: [Failure: String] = [
                .invalid: "Invalid Wi-Fi request", .unavailable: "No host Wi-Fi interface",
                .permission: "Wi-Fi scans require macOS Location permission for LinuxHost",
                .stale: "Scan expired; scan again before connecting",
                .inactive: "Wi-Fi requires the active Linux graphical session",
                .unsupported: "Enterprise Wi-Fi authentication is not implemented",
                .credentials: "A password or key is required for this secured network"]
            return ["ok": false, "error": descriptions[error] ?? "Wi-Fi request failed"]
        } catch {
            let code = (error as NSError).code
            return ["ok": false, "error": "macOS Wi-Fi operation failed", "code": code]
        }
    }

    private func scanPage(id: String, snapshot: Snapshot, offset: Int) -> [String: Any] {
        // Two networks with <=4096-byte IEs fit comfortably within 16 KiB,
        // including base64 expansion and JSON metadata.
        let scanEntries = snapshot.entries
        let start = min(offset, scanEntries.count)
        let end = min(start + 2, scanEntries.count)
        var result: [String: Any] = ["ok": true, "scanID": id,
            "networks": Array(scanEntries[start..<end]), "total": scanEntries.count,
            "truncated": snapshot.truncated,
            "expiresInSeconds": max(0, 60 - Int(now - snapshot.created))]
        if end < scanEntries.count { result["nextOffset"] = end }
        return result
    }
}
