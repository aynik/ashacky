import Foundation
import Darwin

@main enum SidecarServiceChecks {
    static let id = "sidecar:" + String(repeating: "a", count: 63)
    static func wait(_ ready: () -> Bool, timeout: TimeInterval = 3) {
        let deadline = Date().addingTimeInterval(timeout)
        while !ready() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.01)) }
        precondition(ready(), "Sidecar fixture deadline expired")
    }
    static func request(_ service: SidecarService, _ action: String, _ target: String? = nil) -> [String: Any] {
        var response: [String: Any]?
        var value: [String: Any] = ["action": action]
        if let target { value["id"] = target }
        service.request(value) { response = $0 }
        wait { response != nil }
        return response!
    }
    static func main() {
        if CommandLine.arguments.count > 1 {
            // The same binary acts as a fake child. No private Apple API loads.
            switch CommandLine.arguments.last!.last! {
            case "0": sleep(5)
            case "1": print(String(repeating: "x", count: 20000)); return
            case "2": print("{\"ok\":false,\"error\":\"fixture rejection\"}"); exit(1)
            case "4": print("invalid-json"); return
            default: break
            }
            print("{\"ok\":true,\"devices\":[]}"); return
        }
        let helper = URL(fileURLWithPath: CommandLine.arguments[0])
        let service = SidecarService(helper: helper, timeout: 0.3, isActive: { true })
        precondition(request(service, "display-list")["ok"] as? Bool == true)
        precondition(request(service, "display-connect", id + "3")["ok"] as? Bool == true)
        for suffix in ["0", "1", "2", "4"] {
            precondition(request(service, "display-connect", id + suffix)["ok"] as? Bool == false)
        }
        for target in ["bad", id + "3\n", id.uppercased() + "3"] {
            precondition(request(service, "display-connect", target)["ok"] as? Bool == false)
        }
        let inactive = SidecarService(helper: helper, isActive: { false })
        precondition(request(inactive, "display-list")["ok"] as? Bool == false)
        var replies = 0
        service.request(["action": "display-connect", "id": id + "0"]) { result in
            replies += 1; precondition(result["ok"] as? Bool == false)
        }
        precondition(request(service, "display-list")["ok"] as? Bool == false) // Busy.
        service.cancel()
        precondition(replies == 1)
        precondition(request(service, "display-list")["ok"] as? Bool == true)
        RunLoop.main.run(until: Date().addingTimeInterval(0.4))
        precondition(replies == 1) // A late child completion cannot reply twice.
        print("Sidecar helper success, validation, inactive session, timeout, overflow, malformed output, cancellation and busy handling passed")
    }
}
