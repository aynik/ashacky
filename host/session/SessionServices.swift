import AppKit

/// Own every SSH command started by the app, including the long-lived lock watcher.
final class SessionCommands {
    private let lock = NSLock()
    private var stopped = false
    private var processes: [ObjectIdentifier: Process] = [:]

    var running: Bool {
        lock.lock(); defer { lock.unlock() }
        return !stopped
    }

    func launch(_ process: Process) throws -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !stopped else { return false }
        try process.run()
        processes[ObjectIdentifier(process)] = process
        return true
    }

    func finished(_ process: Process) {
        lock.lock(); defer { lock.unlock() }
        processes.removeValue(forKey: ObjectIdentifier(process))
    }

    func stop() {
        lock.lock(); defer { lock.unlock() }
        stopped = true
        for process in processes.values where process.isRunning { process.terminate() }
    }
}

/// Session services share Ashacky's main run loop and macOS application identity.
@objc(AshackySessionServices) public final class AshackySessionServices: NSObject {
    private let commands = SessionCommands()
    private var bridge: Bridge?
    @objc public var statusChanged: (() -> Void)?

    /// Read-only telemetry; power/authentication requests still use authenticated RPC.
    @objc public func statusMessage() -> Data? {
        var data = try? JSONSerialization.data(withJSONObject: ["version": 1, "status": Control.status()])
        data?.append(10)
        return data
    }

    @objc public func start() throws {
        let ssh = ProcessInfo.processInfo.environment["ASHACKY_GUEST_SSH"] ?? ""
        guard ssh.hasPrefix("/"), FileManager.default.fileExists(atPath: ssh) else {
            throw IPCError.message("ASHACKY_GUEST_SSH must select this installation's guest SSH wrapper")
        }
        try Control.start(commands: commands)
        Control.statusChanged = { [weak self] in self?.statusChanged?() }
        let sync = Bridge(guestSSH: ssh, commands: commands)
        bridge = sync
        sync.start()
    }

    @objc public func stop() {
        bridge?.stop()
        commands.stop()
        Control.stop()
    }

    @objc public static func checkTransitions() {
        checkSessionTransitions()
    }
}
