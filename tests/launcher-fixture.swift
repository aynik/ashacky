// Temporary, windowless LaunchServices app for check-launcher.py. It has no
// Ashacky services, entitlements, device access or connection to a VM.
import AppKit
import Darwin

let directory = URL(fileURLWithPath: ProcessInfo.processInfo.environment["ASHACKY_LAUNCHER_CHECK_DIRECTORY"]!)
let mode = CommandLine.arguments.dropFirst().first ?? "hold"
let receipt = URL(fileURLWithPath: ProcessInfo.processInfo.environment["ASHACKY_EXIT_RECEIPT"]!)
func cleanReceipt() { try! Data("clean\n".utf8).write(to: receipt, options: .atomic) }

final class Delegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(to: directory.appendingPathComponent("ready"), atomically: true, encoding: .utf8)
        switch mode {
        case "clean": NSApp.terminate(nil)
        case "dirty": exit(0) // No receipt, even though the OS exit status is zero.
        case "crash": kill(getpid(), SIGKILL)
        default: break
        }
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        mode == "refuse" ? .terminateCancel : .terminateNow
    }
    func applicationWillTerminate(_ notification: Notification) { cleanReceipt() }
}

if mode == "delayed-launch" {
    try! String(getpid()).write(to: directory.appendingPathComponent("born"), atomically: true, encoding: .utf8)
    Thread.sleep(forTimeInterval: 4)
}
let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.run()
