import Foundation
import SystemConfiguration
import Darwin

struct PowerPolicy: Codable { let userID: UInt32; let enabled: Bool; let allowOtherSessions: Bool }

@main enum PowerHelper {
    static var server: UnixServer?
    static var lastAction = Date.distantPast
    static func main() throws {
        guard getuid() == 0 else { fputs("Must run as root\n", stderr); exit(1) }
        signal(SIGPIPE, SIG_IGN)
        let directory = Installation.powerRuntime
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o755])
        let lock = Darwin.open(directory + "/power.lock", O_CREAT | O_RDWR, 0o600)
        guard lock >= 0, flock(lock, LOCK_EX | LOCK_NB) == 0 else { exit(1) }
        server = try UnixServer(path: directory + "/power.sock", mode: 0o666) { fd, request in
            do {
                let policy = try JSONDecoder().decode(PowerPolicy.self, from: Data(contentsOf: URL(fileURLWithPath: Installation.powerPolicy)))
                var uid: uid_t = 0; var gid: gid_t = 0
                guard getpeereid(fd, &uid, &gid) == 0, uid == policy.userID else { return denied("Wrong user") }
                // Authenticate the actual process behind the Unix socket, not a claimed PID.
                var pid: pid_t = 0; var size = socklen_t(MemoryLayout<pid_t>.size)
                guard getsockopt(fd, SOL_LOCAL, LOCAL_PEERPID, &pid, &size) == 0 else { return denied("Missing peer identity") }
                var path = [CChar](repeating: 0, count: 4096)
                guard proc_pidpath(pid, &path, UInt32(path.count)) > 0,
                      Installation.powerClients.contains(String(cString: path)) else { return denied("Untrusted client executable") }
                var activeUID: uid_t = 0
                _ = SCDynamicStoreCopyConsoleUser(nil, &activeUID, nil)
                guard activeUID == policy.userID else { return denied("Linux account is not the active console user") }
                guard policy.enabled else { return denied("Host power controls are disabled in the administrator policy") }
                let action = request["action"] as? String ?? ""
                guard ["check", "sleep", "poweroff", "restart"].contains(action) else { return denied("Unsupported action") }
                let operation = action == "check" ? (request["operation"] as? String ?? "poweroff") : action
                guard ["sleep", "poweroff", "restart"].contains(operation) else { return denied("Unsupported operation") }
                if !policy.allowOtherSessions && operation != "sleep" {
                    // utmpx records include fast-switched graphical sessions and remote sessions.
                    setutxent()
                    defer { endutxent() }
                    while let entry = getutxent() {
                        var record = entry.pointee
                        if record.ut_type == USER_PROCESS {
                            let name = withUnsafePointer(to: &record.ut_user) { ptr in ptr.withMemoryRebound(to: CChar.self, capacity: 256) { String(cString: $0) } }
                            if !name.isEmpty, let other = getpwnam(name), other.pointee.pw_uid != policy.userID {
                                return denied("Another macOS user is logged in; log them out before host power actions")
                            }
                        }
                    }
                }
                if action == "check" { return ["ok": true, "enabled": true] }
                guard ["sleep", "poweroff", "restart"].contains(action) else { return denied("Unsupported action") }
                guard Date().timeIntervalSince(lastAction) > 10 else { return denied("Power action rate limit") }
                lastAction = Date()
                fputs("Authorized \(action) from UID \(uid), PID \(pid)\n", stderr)
                // Reply before executing; never execute request-provided command text.
                DispatchQueue.global().asyncAfter(deadline: .now() + 1) {
                    var currentUID: uid_t = 0
                    _ = SCDynamicStoreCopyConsoleUser(nil, &currentUID, nil)
                    guard currentUID == policy.userID else { fputs("Power action cancelled: session changed\n", stderr); return }
                    let process = Process()
                    if action == "sleep" { process.executableURL = URL(fileURLWithPath: "/usr/bin/pmset"); process.arguments = ["sleepnow"] }
                    else { process.executableURL = URL(fileURLWithPath: "/sbin/shutdown"); process.arguments = [action == "restart" ? "-r" : "-h", "now"] }
                    do { try process.run(); process.waitUntilExit(); fputs("Power command exit: \(process.terminationStatus)\n", stderr) }
                    catch { fputs("Power command failed: \(error)\n", stderr) }
                }
                return ["ok": true]
            } catch { return denied(String(describing: error)) }
        }
        RunLoop.main.run()
    }
    static func denied(_ reason: String) -> [String: Any] { ["ok": false, "error": reason] }
}
