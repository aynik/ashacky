import Foundation
import Darwin

/// The private framework runs in a bounded child process, never on the VM's
/// display/input thread. Children inherit Ashacky's graphical bootstrap session.
final class SidecarService {
    private let helper: URL
    private let isActive: () -> Bool
    private let timeout: TimeInterval
    private var process: Process?
    private var completion: (([String: Any]) -> Void)?
    private var deadline: DispatchWorkItem?
    var available: Bool { FileManager.default.isExecutableFile(atPath: helper.path) }

    init(helper: URL, timeout: TimeInterval = 28, isActive: @escaping () -> Bool) {
        self.helper = helper; self.timeout = timeout; self.isActive = isActive
    }

    func request(_ value: [String: Any], reply: @escaping ([String: Any]) -> Void) {
        precondition(Thread.isMainThread)
        guard isActive() else { reply(["ok": false, "error": "The Mac desktop session is inactive"]); return }
        guard process == nil else { reply(["ok": false, "error": "A display operation is already in progress"]); return }
        guard available else { reply(["ok": false, "error": "Host display controls are unavailable"]); return }
        let arguments: [String]
        switch value["action"] as? String {
        case "display-list": arguments = ["list"]
        case "display-connect", "display-disconnect":
            guard let id = value["id"] as? String, id.utf8.count == 72,
                  id.range(of: "^sidecar:[0-9a-f]{64}$", options: .regularExpression) != nil else {
                reply(["ok": false, "error": "Invalid display destination"]); return
            }
            arguments = [value["action"] as? String == "display-connect" ? "connect" : "disconnect", id]
        default: reply(["ok": false, "error": "Unsupported display action"]); return
        }
        let child = Process(), pipe = Pipe()
        child.executableURL = helper; child.arguments = arguments
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = pipe; child.standardError = FileHandle.nullDevice
        do { try child.run() }
        catch { reply(["ok": false, "error": "Could not start the host display helper"]); return }
        process = child; completion = reply
        let limit = DispatchWorkItem { [weak self, weak child] in
            guard let self, let child, self.process === child else { return }
            self.cancel()
        }
        deadline = limit
        DispatchQueue.main.asyncAfter(deadline: .now() + timeout, execute: limit)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var data = Data(), overflow = false
            let reader = pipe.fileHandleForReading
            while true {
                let chunk = reader.readData(ofLength: 4096)
                if chunk.isEmpty { break }
                if data.count + chunk.count > 16384 {
                    overflow = true
                    if child.isRunning { kill(child.processIdentifier, SIGKILL) }
                    break
                }
                data.append(chunk)
            }
            try? reader.close()
            child.waitUntilExit()
            var result: [String: Any] = ["ok": false, "error": "Display helper stopped; connection outcome is unknown. Refresh the list before retrying."]
            if !overflow, let decoded = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let ok = decoded["ok"] as? Bool, !ok || child.terminationStatus == 0 { result = decoded }
            DispatchQueue.main.async { [weak self] in
                guard let self, self.process === child else { return }
                self.finish(result)
            }
        }
    }

    private func finish(_ value: [String: Any]) {
        deadline?.cancel(); deadline = nil
        let reply = completion
        process = nil; completion = nil
        reply?(value)
    }

    func cancel() {
        precondition(Thread.isMainThread)
        guard let child = process else { return }
        if child.isRunning { kill(child.processIdentifier, SIGKILL) }
        finish(["ok": false, "error": "Display operation ended; connection outcome is unknown. Refresh the list before retrying."])
    }
}
