import AppKit
import Darwin

// Launch through macOS so TCC attributes permissions to Ashacky.app. This
// background executable mirrors app exit/crash to the Python VM supervisor.
let environment = ProcessInfo.processInfo.environment
guard let directory = environment["ASHACKY_PRIVATE_DIRECTORY"] else {
    fputs("Missing Ashacky private directory\n", stderr); exit(1)
}
let runtime = URL(fileURLWithPath: directory).appendingPathComponent("runtime")
var info = stat()
guard lstat(runtime.path, &info) == 0, info.st_uid == getuid(),
      info.st_mode & 0o077 == 0, info.st_mode & S_IFMT == S_IFDIR else {
    fputs("Invalid private runtime directory\n", stderr); exit(1)
}
let receipt = runtime.appendingPathComponent("display-exit-" + UUID().uuidString)
let appURL = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
let configuration = NSWorkspace.OpenConfiguration()
configuration.createsNewApplicationInstance = true
configuration.allowsRunningApplicationSubstitution = false
configuration.addsToRecentItems = false
configuration.promptsUserIfNeeded = false
configuration.arguments = Array(CommandLine.arguments.dropFirst())
configuration.environment = environment.merging([
    "ASHACKY_EXIT_RECEIPT": receipt.path,
    "ASHACKY_LAUNCHER_PID": String(getpid()),
    "ASHACKY_DISPLAY_LOG": runtime.appendingPathComponent("display.log").path
]) { _, value in value }
var application: NSRunningApplication?
var stopping = false
var stopTime: TimeInterval?
var signals: [DispatchSourceSignal] = []
for number in [SIGTERM, SIGINT] {
    signal(number, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
    source.setEventHandler {
        stopping = true
        stopTime = ProcessInfo.processInfo.systemUptime
        application?.terminate()
    }
    source.resume(); signals.append(source)
}
NSWorkspace.shared.openApplication(at: appURL, configuration: configuration) { running, error in
    DispatchQueue.main.async {
        guard let running else {
            fputs("Ashacky launch failed: \(error?.localizedDescription ?? "unknown error")\n", stderr)
            exit(1)
        }
        application = running
        if stopping { running.terminate() }
        print("Ashacky app launched: PID \(running.processIdentifier)")
        fflush(stdout)
    }
}
let timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { _ in
    guard let application else { return }
    if application.isTerminated {
        let clean = (try? String(contentsOf: receipt, encoding: .utf8)) == "clean\n"
        try? FileManager.default.removeItem(at: receipt)
        exit(clean || stopping ? 0 : 1)
    }
    if let stopTime, ProcessInfo.processInfo.systemUptime - stopTime > 3 {
        application.forceTerminate()
    }
}
RunLoop.main.run()
