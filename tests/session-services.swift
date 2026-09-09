import Foundation

@main enum SessionServiceChecks {
    static func main() throws {
        checkControlChannel()
        try checkFIDO2Storage()

        // Moving control into the GUI process must retain its authentication
        // and clean-shutdown gates. None of these requests performs an action.
        Control.config = ["token": String(repeating: "x", count: 32), "powerEnabled": false]
        let message = AshackySessionServices().statusMessage(audioRevision: "test-epoch:1", bluetoothRevision: "test-bt:2", wifiRevision: "test-wifi:3")!
        precondition(message.last == 10 && message.count < 16384)
        let snapshot = try JSONSerialization.jsonObject(with: message) as! [String: Any]
        precondition(snapshot["version"] as? Int == 1)
        let status = snapshot["status"] as! [String: Any]
        precondition(status["ok"] as? Bool == true)
        precondition(status["audioRevision"] as? String == "test-epoch:1")
        precondition(status["bluetoothRevision"] as? String == "test-bt:2")
        precondition(status["wifiRevision"] as? String == "test-wifi:3")
        let legacy = AshackySessionServices().statusMessage(audioRevision: nil, bluetoothRevision: nil, wifiRevision: nil)!
        let legacyStatus = (try JSONSerialization.jsonObject(with: legacy) as! [String: Any])["status"] as! [String: Any]
        precondition(legacyStatus["wifiRevision"] == nil)
        precondition(status["token"] == nil && status["powerEnabled"] == nil)
        precondition(!String(decoding: message, as: UTF8.self).contains(Control.config["token"] as! String))
        // Stub the native operation: validate gates without locking this Mac.
        let originalLock = Control.lockScreen
        defer { Control.lockScreen = originalLock }
        var locks = 0
        Control.lockScreen = { locks += 1; return true }
        Control.handle(["action": "lock", "token": "wrong"]) {
            precondition($0["ok"] as? Bool == false)
        }
        precondition(locks == 0)
        let active = Control.active()
        Control.handle(["action": "lock", "token": Control.config["token"]!]) {
            precondition($0["ok"] as? Bool == active)
        }
        precondition(locks == (active ? 1 : 0))
        Control.lockScreen = { false }
        Control.handle(["action": "lock", "token": Control.config["token"]!]) {
            precondition($0["ok"] as? Bool == false)
        }
        Control.pending = "poweroff"
        Control.deadline = Date().addingTimeInterval(60)
        Control.handle(["action": "vm-stopped", "token": "wrong", "clean": true]) {
            precondition($0["ok"] as? Bool == false)
        }
        precondition(Control.pending == "poweroff")
        Control.handle(["action": "vm-stopped", "token": Control.config["token"]!, "clean": false]) {
            precondition($0["ok"] as? Bool == true)
        }
        precondition(Control.pending == nil)
        precondition(Control.power("check")["ok"] as? Bool == false)
        print("Direct channel lifecycle, host lock, token validation and clean-shutdown gates passed")
    }
}
