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
var stopDeadline: TimeInterval?
var terminationObservation: NSKeyValueObservation?
var forceStop: DispatchWorkItem?
// NSRunningApplication.terminated is KVO-observable. Include its initial
// value so a fast exit before observer registration cannot be missed.
func applicationExited(_ running: NSRunningApplication) {
    guard running.isTerminated else { return }
    forceStop?.cancel()
    terminationObservation?.invalidate()
    let clean = (try? String(contentsOf: receipt, encoding: .utf8)) == "clean\n"
    try? FileManager.default.removeItem(at: receipt)
    exit(clean || stopping ? 0 : 1)
}

func stopApplication() {
    guard let application, let stopDeadline else { return }
    applicationExited(application)
    application.terminate()
    guard forceStop == nil else { return }
    let force = DispatchWorkItem {
        applicationExited(application)
        application.forceTerminate()
    }
    forceStop = force
    // If shutdown began before LaunchServices replied, only the remaining
    // grace period is available. Repeated signals never extend the deadline.
    let remaining = max(0, stopDeadline - ProcessInfo.processInfo.systemUptime)
    DispatchQueue.main.asyncAfter(deadline: .now() + remaining, execute: force)
}

var signals: [DispatchSourceSignal] = []
for number in [SIGTERM, SIGINT] {
    signal(number, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
    source.setEventHandler {
        guard !stopping else { return }
        stopping = true
        stopDeadline = ProcessInfo.processInfo.systemUptime + 3
        stopApplication()
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
        terminationObservation = running.observe(\.isTerminated, options: [.initial, .new]) { observed, _ in
            DispatchQueue.main.async { applicationExited(observed) }
        }
        if stopping { stopApplication() }
        print("Ashacky app launched: PID \(running.processIdentifier)")
        fflush(stdout)
    }
}
RunLoop.main.run()
