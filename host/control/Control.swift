import Foundation
import AppKit
import LocalAuthentication
import SystemConfiguration
import IOKit.ps
import Darwin
import CoreServices

// Keep the native login framework loaded while its function pointer is used.
private let loginFramework = dlopen("/System/Library/PrivateFrameworks/login.framework/Versions/A/login", RTLD_LAZY)
func lockHostScreen() -> Bool {
    guard let framework = loginFramework, let symbol = dlsym(framework, "SACLockScreenImmediate") else { return false }
    let lock = unsafeBitCast(symbol, to: (@convention(c) () -> Void).self)
    lock()
    return true
}

enum Control {
    static var server: UnixServer?
    static var supervisor: UnixServer?
    static var config: [String: Any] = [:]
    static var wake = 0
    static var sleeping = false
    static var pending: String?
    static var deadline = Date.distantPast
    static var authBusy = false
    static var qemuPID: Int32 = 0
    static var observers: [NSObjectProtocol] = []
    static var processLock: ServiceProcessLock?
    static var timer: Timer?
    static var lockScreen: () -> Bool = lockHostScreen
    static var statusChanged: (() -> Void)?
    static var powerObserver: PowerObserver?
    static var sidecar: SidecarService?
    static var enabled: Bool { config["powerEnabled"] as? Bool == true }
    static func active() -> Bool {
        var uid: uid_t = 0
        _ = SCDynamicStoreCopyConsoleUser(nil, &uid, nil)
        return uid == getuid()
    }
    static func power(_ action: String, operation: String? = nil) -> [String:Any] {
        guard enabled else { return ["ok":false,"error":"Power actions disabled for this test session"] }
        var request: [String:Any] = ["action":action]
        if let op = operation { request["operation"] = op }
        return (try? requestSocket(config["powerSocket"] as? String ?? "",request)) ?? ["ok":false,"error":"Power helper unavailable"]
    }
    static func request(_ value: [String:Any]) -> [String:Any] {
        let semaphore=DispatchSemaphore(value:0)
        var answer: [String:Any] = ["ok":false,"error":"Host request timed out"]
        DispatchQueue.main.async { handle(value) { answer=$0; semaphore.signal() } }
        _ = semaphore.wait(timeout:.now()+80)
        return answer
    }
    static func status() -> [String:Any] {
        var result: [String:Any] = ["ok":true,"active":active(),"wakeGeneration":wake,"sleeping":sleeping,"pendingPower":pending ?? "none","backend":"qemu-spice","bundleID":Bundle.main.bundleIdentifier ?? ""]
        if let info=IOPSCopyPowerSourcesInfo()?.takeRetainedValue(), let sources=IOPSCopyPowerSourcesList(info)?.takeRetainedValue() as? [CFTypeRef] {
            for source in sources {
                if let battery=IOPSGetPowerSourceDescription(info,source)?.takeUnretainedValue() as? [String:Any] {
                    let keys=["Current Capacity","Max Capacity","Is Charging","Power Source State"]
                    result["battery"]=battery.filter { keys.contains($0.key) }; break
                }
            }
        }
        return result
    }
    static func handle(_ value: [String:Any], reply: @escaping ([String:Any])->Void) {
        guard value["token"] as? String == config["token"] as? String else { reply(["ok":false,"error":"Invalid session token"]); return }
        let action=value["action"] as? String ?? ""
        if action=="status" { reply(status()); return }
        guard active() else { pending=nil; reply(["ok":false,"error":"The Linux macOS session is not active"]); return }
        switch action {
        case "display-list", "display-connect", "display-disconnect":
            guard let sidecar else { reply(["ok": false, "error": "Host display controls are unavailable"]); return }
            sidecar.request(value, reply: reply)
        case "lock":
            sidecar?.cancel()
            if lockScreen() { reply(["ok": true]); NSLog("Host lock requested from Linux") }
            else { reply(["ok": false, "error": "macOS screen locking is unavailable"]) }
        case "powercheck": reply(power("check",operation:"poweroff"))
        case "sleep":
            let check=power("check",operation:"sleep")
            guard check["ok"] as? Bool == true else { reply(check); return }
            reply(["ok":true]); DispatchQueue.global().asyncAfter(deadline:.now()+1) { _=power("sleep") }
        case "poweroff", "restart", "logout":
            guard pending==nil else { reply(["ok":false,"error":"A session action is already pending"]); return }
            guard enabled else { reply(["ok":false,"error":"Session actions disabled for this test"]); return }
            if action != "logout" {
                let check=power("check",operation:action)
                guard check["ok"] as? Bool == true else { reply(check); return }
            }
            pending=action; deadline=Date().addingTimeInterval(120)
            // Guest powers off cleanly; supervisor confirms QMP guest shutdown
            // before asking this service to perform the pending host action.
            reply(["ok":true,"shutdownGuest":true])
        case "vm-stopped":
            guard let operation=pending, Date()<deadline, value["clean"] as? Bool == true else { pending=nil; reply(["ok":true]); return }
            pending=nil; reply(["ok":true])
            DispatchQueue.main.asyncAfter(deadline:.now()+1) {
                guard active() else { return }
                if operation=="logout" {
                    // GNOME already confirmed logout. The system-process target
                    // takes kAEReallyLogOut. Use kSystemProcess (1), never
                    // kCurrentProcess (2), which sends the request to this helper.
                    let psn = [UInt32(0), UInt32(kSystemProcess)]
                    let target = psn.withUnsafeBytes { NSAppleEventDescriptor(descriptorType: DescType(typeProcessSerialNumber), bytes: $0.baseAddress!, length: $0.count) }
                    let event = NSAppleEventDescriptor(eventClass: AEEventClass(kCoreEventClass), eventID: AEEventID(kAEReallyLogOut), targetDescriptor: target, returnID: -1, transactionID: 0)
                    do { _ = try event.sendEvent(options: [.noReply], timeout: 5) }
                    catch { NSLog("Logout event failed: %@",String(describing:error)) }
                } else { DispatchQueue.global().async { _=power(operation) } }
            }
        case "authenticate":
            guard !authBusy else { reply(["ok":false,"error":"Authentication is already in progress"]); return }
            let reasons=["sudo":"authorize administrator access inside Linux","unlock":"unlock your Linux desktop","test":"test Linux Touch ID integration"]
            guard let purpose=value["purpose"] as? String, let reason=reasons[purpose] else { reply(["ok":false,"error":"Unsupported authentication purpose"]); return }
            let context=LAContext();context.localizedFallbackTitle=""
            var error: NSError?
            guard context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics,error:&error) else { reply(["ok":false,"error":error?.localizedDescription ?? "Touch ID unavailable"]);return }
            authBusy=true
            context.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics,localizedReason:reason) { success,error in
                DispatchQueue.main.async { authBusy=false;reply(["ok":success && active(),"error":error?.localizedDescription ?? "none"]) }
            }
            DispatchQueue.main.asyncAfter(deadline:.now()+60) { context.invalidate() }
        default: reply(["ok":false,"error":"Unsupported action"])
        }
    }
    static func start() throws {
        let path=ProcessInfo.processInfo.environment["LINUXHOST_CONTROL_CONFIG"] ?? ""
        let data=try Data(contentsOf:URL(fileURLWithPath:path))
        guard let value=try JSONSerialization.jsonObject(with:data) as? [String:Any] else { throw IPCError.message("Invalid control configuration") }
        config=value
        guard let endpoint=config["socket"] as? String, let token=config["token"] as? String, token.count>=32 else { throw IPCError.message("Invalid control configuration") }
        signal(SIGPIPE,SIG_IGN)
        if let executable = Bundle.main.executableURL {
            sidecar = SidecarService(helper: executable.deletingLastPathComponent().appendingPathComponent("AshackySidecar"),
                                     isActive: { active() && !sleeping })
        }
        processLock=try ServiceProcessLock(path:endpoint+".lock")
        server=try UnixServer(path:endpoint,mode:0o600) { fd,value in
            var uid:uid_t=0;var gid:gid_t=0
            guard getpeereid(fd,&uid,&gid)==0,uid==getuid() else { return ["ok":false,"error":"Wrong session user"] }
            if value["action"] as? String == "vm-stopped" { return ["ok":false,"error":"Supervisor endpoint required"] }
            return request(value)
        }
        supervisor=try UnixServer(path:endpoint+".supervisor",mode:0o600) { fd,value in
            var uid:uid_t=0;var gid:gid_t=0
            guard getpeereid(fd,&uid,&gid)==0,uid==getuid(),value["action"] as? String == "vm-stopped" else { return ["ok":false,"error":"Invalid supervisor request"] }
            return request(value)
        }
        let center=NSWorkspace.shared.notificationCenter
        observers.append(center.addObserver(forName:NSWorkspace.willSleepNotification,object:nil,queue:.main) { _ in sleeping=true;sidecar?.cancel();statusChanged?() })
        observers.append(center.addObserver(forName:NSWorkspace.didWakeNotification,object:nil,queue:.main) { _ in sleeping=false;wake+=1;statusChanged?() })
        observers.append(center.addObserver(forName:NSWorkspace.sessionDidResignActiveNotification,object:nil,queue:.main) { _ in pending=nil;sidecar?.cancel();statusChanged?() })
        observers.append(center.addObserver(forName:NSWorkspace.sessionDidBecomeActiveNotification,object:nil,queue:.main) { _ in statusChanged?() })
        powerObserver=PowerObserver { statusChanged?() }
        if powerObserver == nil { NSLog("Power notifications unavailable; status heartbeat remains active") }
        timer=Timer.scheduledTimer(withTimeInterval:2,repeats:true) { _ in if pending != nil && Date()>deadline { pending=nil } }
        NSLog("Ashacky control ready")
    }
    static func stop() {
        sidecar?.cancel(); sidecar=nil
        pending=nil
        statusChanged=nil
        powerObserver?.stop();powerObserver=nil
        timer?.invalidate(); timer=nil
        for observer in observers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        observers.removeAll()
        server=nil; supervisor=nil; processLock=nil
    }
}
