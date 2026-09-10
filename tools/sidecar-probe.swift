import AppKit
import SystemConfiguration
import Darwin

/// One-operation helper, also usable for attended development checks. The app
/// launches its bundled copy directly; SSH is never a runtime transport.
@main struct SidecarProbe {
    static var backend: SidecarBackend?

    static func finish(_ result: [String: Any], code: Int32 = 0) -> Never {
        let data = try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        FileHandle.standardOutput.write(data + Data([10]))
        exit(code)
    }

    static func active() -> Bool {
        var uid: uid_t = 0
        guard SCDynamicStoreCopyConsoleUser(nil, &uid, nil) != nil else { return false }
        return uid == getuid()
    }

    static func screenCounts() -> [String: Any] {
        var count: UInt32 = 0
        var displays = [CGDirectDisplayID](repeating: 0, count: 32)
        guard CGGetActiveDisplayList(UInt32(displays.count), &displays, &count) == .success,
              count < displays.count else { return ["known": false] }
        return ["known": true, "total": Int(count),
                "external": displays.prefix(Int(count)).filter { CGDisplayIsBuiltin($0) == 0 }.count]
    }

    static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        if args == ["capacity"] { print(ASHACKY_DISPLAY_LIMIT); return }
        guard (args.count == 1 && args[0] == "list") ||
              (args.count == 2 && ["connect", "disconnect"].contains(args[0]) &&
               args[1].range(of: "^sidecar:[0-9a-f]{64}$", options: .regularExpression) != nil) else {
            finish(["ok": false, "error": "Usage: sidecar-probe list | connect <id> | disconnect <id>"], code: 2)
        }
        guard active() else { finish(["ok": false, "error": "The invoking user must own the active Mac desktop"], code: 2) }
        // A process-level deadline also covers a stuck synchronous private getter.
        // No timer/DispatchGroup on the main thread can provide that guarantee.
        signal(SIGALRM, SIG_DFL)
        alarm(25)
        do {
            let driver = try SidecarBackend(); backend = driver
            let snapshot = try driver.snapshot()
            if args[0] == "list" {
                finish(["ok": true, "devices": snapshot, "screens": screenCounts()])
            }
            guard let target = snapshot.first(where: { $0["id"] as? String == args[1] }) else {
                finish(["ok": false, "error": "Device is no longer available; list destinations again"], code: 2)
            }
            let connected = target["connected"] as? Bool == true
            if connected == (args[0] == "connect") {
                finish(["ok": true, "unchanged": true, "devices": snapshot, "screens": screenCounts()])
            }
            if args[0] == "connect" {
                let screens = screenCounts()
                let total = UInt32(clamping: screens["total"] as? Int ?? 0)
                guard ashacky_can_add_sidecar(screens["known"] as? Bool == true, total,
                      snapshot.contains(where: { $0["connected"] as? Bool == true })) else {
                    finish(["ok": false, "error": "No virtual display output is available, or another iPad is already connected"], code: 2)
                }
            }
            guard active() else { finish(["ok": false, "error": "Mac session changed"], code: 2) }
            try driver.perform(args[0], id: args[1]) { error in
                if let error {
                    // Do not dump private NSError userInfo or device identities.
                    finish(["ok": false, "error": "Native Sidecar operation failed", "domain": error.domain,
                            "code": error.code, "screens": screenCounts()], code: 1)
                }
                finish(["ok": true, "operation": args[0], "devices": (try? driver.snapshot()) ?? [],
                        "screens": screenCounts()])
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 20) {
                finish(["ok": false, "error": "Sidecar completion timed out; outcome is unknown. List before retrying."], code: 3)
            }
            RunLoop.main.run()
        } catch {
            finish(["ok": false, "error": "Sidecar private API or device data is unavailable"], code: 1)
        }
    }
}
