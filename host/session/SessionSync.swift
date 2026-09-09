import AppKit
import CoreGraphics
import SystemConfiguration

// GUI-session-local observation. Wake itself never authorizes an unlock.
struct Snapshot {
    let locked: Bool
    let eligible: Bool
}
func snapshot() -> Snapshot? {
    guard let d=CGSessionCopyCurrentDictionary() as? [String:Any],
          let uid=d["kCGSSessionUserIDKey"] as? NSNumber, uid.uint32Value==getuid() else { return nil }
    var console: uid_t=0
    _=SCDynamicStoreCopyConsoleUser(nil,&console,nil)
    return Snapshot(locked:(d["CGSSessionScreenIsLocked"] as? Bool)==true,
        eligible:console==getuid() && (d["kCGSSessionOnConsoleKey"] as? Bool)==true && (d["kCGSessionLoginDoneKey"] as? Bool)==true)
}
func shouldLockHost(previous:Bool?, locked:Bool, origin:String, host:Snapshot?, sawLock:Bool) -> Bool {
    guard previous==false,locked,origin=="guest",let host=host else { return false }
    return host.eligible && !host.locked && !sawLock
}
private let loginFramework=dlopen("/System/Library/PrivateFrameworks/login.framework/Versions/A/login",RTLD_LAZY)
final class Bridge {
    let guestSSH: String
    let commands: SessionCommands
    var timer: Timer?
    var observers: [NSObjectProtocol] = []
    var sawLock=false
    var generation=0
    var guestLocked:Bool?=nil
    let queue=DispatchQueue(label:"local.linuxhost.session-sync.ssh")
    init(guestSSH: String, commands: SessionCommands) {
        self.guestSSH=guestSSH; self.commands=commands
    }
    func send(_ action:String, generation expected:Int) {
        let expires=Date().addingTimeInterval(20)
        queue.async {
            if action=="unlock" {
                var allowed=false
                DispatchQueue.main.sync {
                    if let s=snapshot() { allowed=self.generation==expected && !s.locked && s.eligible && Date()<expires }
                }
                guard allowed else { NSLog("Session sync: cancelled stale unlock");return }
            }
            let p=Process();p.executableURL=URL(fileURLWithPath:"/bin/bash")
            p.arguments=[self.guestSSH,"/usr/local/libexec/linuxhost-session-sync",action]
            p.standardInput=FileHandle.nullDevice
            do {
                guard try self.commands.launch(p) else { return }
                defer { self.commands.finished(p) }
                let limit=Date().addingTimeInterval(20)
                while p.isRunning && Date()<limit { Thread.sleep(forTimeInterval:0.1) }
                if p.isRunning { p.terminate(); NSLog("Session sync: %@ timed out",action) }
                else { NSLog("Session sync: %@ exit=%d",action,p.terminationStatus) }
            } catch { NSLog("Session sync: %@ failed: %@",action,String(describing:error)) }
        }
    }
    func guestState(_ value:[String:Any]) {
        guard commands.running else { return }
        guard let locked=value["locked"] as? Bool else { return }
        let previous=guestLocked;guestLocked=locked
        guard shouldLockHost(previous:previous,locked:locked,origin:value["origin"] as? String ?? "",host:snapshot(),sawLock:sawLock) else { return }
        guard let handle=loginFramework,
              let symbol=dlsym(handle,"SACLockScreenImmediate") else {
            NSLog("Session sync: macOS lock API unavailable");return
        }
        // Keep login.framework loaded for the lifetime of this GUI helper.
        let lockScreen=unsafeBitCast(symbol,to:(@convention(c) ()->Void).self)
        NSLog("Session sync: Debian locked; locking macOS")
        lockScreen()
    }
    func watchGuest() {
        DispatchQueue.global(qos:.utility).async {
            while self.commands.running {
                let p=Process();let pipe=Pipe()
                p.executableURL=URL(fileURLWithPath:"/bin/bash")
                p.arguments=[self.guestSSH,"/usr/local/libexec/linuxhost-session-sync","watch"]
                p.standardInput=FileHandle.nullDevice;p.standardOutput=pipe
                DispatchQueue.main.sync { self.guestLocked=nil }
                do {
                    guard try self.commands.launch(p) else { return }
                    defer { self.commands.finished(p) }
                    var buffer=Data()
                    while true {
                        let chunk=pipe.fileHandleForReading.availableData
                        if chunk.isEmpty { break }
                        buffer.append(chunk)
                        if buffer.count>65536 { p.terminate();break }
                        while let end=buffer.firstIndex(of:10) {
                            let line=Data(buffer[..<end]);buffer.removeSubrange(...end)
                            if let state=(try? JSONSerialization.jsonObject(with:line)) as? [String:Any] {
                                DispatchQueue.main.async { self.guestState(state) }
                            }
                        }
                    }
                    if p.isRunning { p.terminate() }
                } catch { NSLog("Session sync: guest watcher failed: %@",String(describing:error)) }
                try? pipe.fileHandleForReading.close()
                Thread.sleep(forTimeInterval:3)
            }
        }
    }
    func sample() {
        guard commands.running else { return }
        guard let s=snapshot() else { return }
        if s.locked {
            if !sawLock { sawLock=true;generation+=1;NSLog("Session sync: observed macOS lock");send("lock",generation:generation) }
        } else if sawLock && s.eligible {
            sawLock=false;generation+=1;NSLog("Session sync: observed macOS unlock");send("unlock",generation:generation)
        }
    }
    func start() {
        timer=Timer.scheduledTimer(withTimeInterval:0.5,repeats:true) { [weak self] _ in self?.sample() }
        let notifications=DistributedNotificationCenter.default()
        observers=["com.apple.screenIsLocked","com.apple.screenIsUnlocked"].map { name in
            notifications.addObserver(forName:Notification.Name(name),object:nil,queue:.main) { [weak self] _ in self?.sample() }
        }
        sample(); watchGuest()
        NSLog("Session sync: ready inside Ashacky; no startup unlock")
    }
    func stop() {
        generation+=1
        timer?.invalidate(); timer=nil
        for observer in observers { DistributedNotificationCenter.default().removeObserver(observer) }
        observers.removeAll()
    }
}
func checkSessionTransitions() {
    let open=Snapshot(locked:false,eligible:true)
    precondition(shouldLockHost(previous:false,locked:true,origin:"guest",host:open,sawLock:false))
    precondition(!shouldLockHost(previous:nil,locked:true,origin:"guest",host:open,sawLock:false))
    precondition(!shouldLockHost(previous:true,locked:true,origin:"guest",host:open,sawLock:false))
    precondition(!shouldLockHost(previous:false,locked:true,origin:"host",host:open,sawLock:false))
    precondition(!shouldLockHost(previous:false,locked:false,origin:"guest",host:open,sawLock:false))
    precondition(!shouldLockHost(previous:false,locked:true,origin:"guest",host:Snapshot(locked:true,eligible:true),sawLock:false))
    precondition(!shouldLockHost(previous:false,locked:true,origin:"guest",host:Snapshot(locked:false,eligible:false),sawLock:false))
    precondition(!shouldLockHost(previous:false,locked:true,origin:"guest",host:open,sawLock:true))
    print("Reverse-lock transition checks passed")
}
