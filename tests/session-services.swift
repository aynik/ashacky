import Foundation

@main enum SessionServiceChecks {
    static func main() throws {
        checkSessionTransitions()

        // Ending an app session must terminate its watcher and reject late
        // queued launches, without touching the real host or guest lock state.
        let commands = SessionCommands()
        let child = Process()
        child.executableURL = URL(fileURLWithPath: "/bin/sleep")
        child.arguments = ["30"]
        let launched = try commands.launch(child)
        precondition(launched)
        commands.stop()
        let limit = Date().addingTimeInterval(3)
        while child.isRunning && Date() < limit { Thread.sleep(forTimeInterval: 0.02) }
        precondition(!child.isRunning, "Session command survived app shutdown")
        commands.finished(child)
        let late = Process()
        late.executableURL = URL(fileURLWithPath: "/usr/bin/true")
        let launchedLate = try commands.launch(late)
        precondition(!launchedLate)

        // Moving control into the GUI process must retain its authentication
        // and clean-shutdown gates. None of these requests performs an action.
        Control.config = ["token": String(repeating: "x", count: 32), "powerEnabled": false]
        let message = AshackySessionServices().statusMessage(audioRevision: "test-epoch:1")!
        precondition(message.last == 10 && message.count < 16384)
        let snapshot = try JSONSerialization.jsonObject(with: message) as! [String: Any]
        precondition(snapshot["version"] as? Int == 1)
        let status = snapshot["status"] as! [String: Any]
        precondition(status["ok"] as? Bool == true)
        precondition(status["audioRevision"] as? String == "test-epoch:1")
        precondition(status["token"] == nil && status["powerEnabled"] == nil)
        precondition(!String(decoding: message, as: UTF8.self).contains(Control.config["token"] as! String))
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
        print("Session child cleanup, token validation and clean-shutdown gates passed")
    }
}
