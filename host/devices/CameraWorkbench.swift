import AppKit
import AVFoundation
import SystemConfiguration

// Local prototype: capture only for a same-user client, with TCC permission,
// in the active console session. Never records frames to disk.
final class CameraWorkbench: NSObject, NSApplicationDelegate, AVCaptureVideoDataOutputSampleBufferDelegate {
    let control = DispatchQueue(label: "local.linuxhost.camera.control")
    let frames = DispatchQueue(label: "local.linuxhost.camera.frames")
    let lock = NSLock()
    var processLock: WorkbenchProcessLock?
    var server: RawUnixServer?
    var session: AVCaptureSession?
    var client: Int32 = -1
    var activeOutput: AVCaptureOutput?
    var count: UInt32 = 0
    var window: NSWindow?
    let status = NSTextField(wrappingLabelWithString: "Allow Camera access, then the Linux test can request a live stream. Nothing is recorded. Each test stops after 30 seconds.")

    static func active() -> Bool {
        var uid: uid_t = 0
        _ = SCDynamicStoreCopyConsoleUser(nil, &uid, nil)
        return uid == getuid()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let directory = CommandLine.arguments.dropFirst().first(where: { !$0.hasPrefix("--") }) ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Ashacky/devices").path
            var info = stat()
            guard lstat(directory, &info) == 0, info.st_uid == getuid(),
                  info.st_mode & 0o077 == 0, info.st_mode & S_IFMT == S_IFDIR else { throw CameraError.invalid }
            processLock = try WorkbenchProcessLock(path: directory + "/camera-workbench.lock")
            server = try RawUnixServer(path: directory + "/camera-workbench.sock") { [self] fd in
                var uid: uid_t = 0, gid: gid_t = 0
                guard getpeereid(fd, &uid, &gid) == 0, uid == getuid() else { close(fd); return }
                prepareSocket(fd, seconds: 2)
                control.async { self.start(fd) }
            }
        } catch { NSApp.terminate(nil); return }
        if CommandLine.arguments.contains("--service") {
            if Self.active(), AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
                authorize() // Permission only; capture still requires an authorized reader.
            }
            return
        }
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 500, height: 180), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        win.title = "LinuxHost camera test"
        status.frame = NSRect(x: 24, y: 72, width: 452, height: 84)
        let button = NSButton(title: "Allow Camera access", target: self, action: #selector(authorize))
        button.frame = NSRect(x: 24, y: 24, width: 240, height: 32)
        win.contentView?.addSubview(status); win.contentView?.addSubview(button)
        window = win; win.center(); win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc func authorize() {
        AVCaptureDevice.requestAccess(for: .video) { [self] allowed in
            message(allowed ? "Camera access allowed. Waiting for the local Linux test." : "Camera access denied. Enable CameraWorkbench in macOS Privacy & Security > Camera.")
        }
    }
    func message(_ text: String) { DispatchQueue.main.async { self.status.stringValue = text } }
    enum CameraError: Error { case invalid }
    func start(_ fd: Int32) {
        guard session == nil, Self.active(), AVCaptureDevice.authorizationStatus(for: .video) == .authorized else { close(fd); return }
        do {
            let discovery = AVCaptureDevice.DiscoverySession(deviceTypes: [.builtInWideAngleCamera], mediaType: .video, position: .unspecified)
            guard let camera = discovery.devices.first else { throw CameraError.invalid }
            let capture = AVCaptureSession()
            capture.beginConfiguration()
            capture.sessionPreset = .vga640x480
            let input = try AVCaptureDeviceInput(device: camera)
            guard capture.canAddInput(input) else { throw CameraError.invalid }
            capture.addInput(input)
            let output = AVCaptureVideoDataOutput()
            output.alwaysDiscardsLateVideoFrames = true
            output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange]
            output.setSampleBufferDelegate(self, queue: frames)
            guard capture.canAddOutput(output) else { throw CameraError.invalid }
            capture.addOutput(output); capture.commitConfiguration()
            session = capture
            lock.lock(); client = fd; activeOutput = output; count = 0; lock.unlock()
            capture.startRunning()
            message("Camera is streaming to the local Linux test. It will stop within 30 seconds.")
            control.asyncAfter(deadline: .now() + 30) { [weak self, weak capture] in
                guard let self, let capture, self.session === capture else { return }
                self.stop()
            }
        } catch {
            close(fd); message("Could not start the built-in camera.")
        }
    }
    func stop(expectedOutput: AVCaptureOutput? = nil) {
        lock.lock()
        if let expectedOutput, activeOutput !== expectedOutput { lock.unlock(); return }
        let fd = client; client = -1; activeOutput = nil; let sent = count; lock.unlock()
        session?.stopRunning(); session = nil
        if fd >= 0 { close(fd) }
        message("Camera stopped. Sent \(sent) frames; no recording was saved.")
    }
    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer, from connection: AVCaptureConnection) {
        lock.lock(); defer { lock.unlock() }
        guard client >= 0, activeOutput === output else { return }
        guard Self.active(), let pixel = CMSampleBufferGetImageBuffer(sample),
              CVPixelBufferGetPixelFormatType(pixel) == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
              CVPixelBufferGetPlaneCount(pixel) == 2 else { control.async { self.stop(expectedOutput: output) }; return }
        let width = CVPixelBufferGetWidth(pixel), height = CVPixelBufferGetHeight(pixel)
        guard width > 0, height > 0, width <= 1280, height <= 720, width % 2 == 0, height % 2 == 0 else { control.async { self.stop(expectedOutput: output) }; return }
        CVPixelBufferLockBaseAddress(pixel, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixel, .readOnly) }
        var data = Data("LHCV0001".utf8)
        for value in [UInt32(width), UInt32(height), UInt32(width * height * 3 / 2), count] {
            var little = value.littleEndian
            withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
        }
        for plane in 0...1 {
            guard let base = CVPixelBufferGetBaseAddressOfPlane(pixel, plane) else { return }
            let rows = plane == 0 ? height : height / 2
            let stride = CVPixelBufferGetBytesPerRowOfPlane(pixel, plane)
            for row in 0..<rows { data.append(base.advanced(by: row * stride).assumingMemoryBound(to: UInt8.self), count: width) }
        }
        let okay = data.withUnsafeBytes { bytes -> Bool in
            var offset = 0
            while offset < bytes.count {
                let n = Darwin.write(client, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
                if n <= 0 { return false }
                offset += n
            }
            return true
        }
        if okay { count &+= 1 } else { control.async { self.stop(expectedOutput: output) } }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { !CommandLine.arguments.contains("--service") }
}

@main enum CameraMain {
    static func main() {
        signal(SIGPIPE, SIG_IGN)
        let app = NSApplication.shared, delegate = CameraWorkbench()
        app.setActivationPolicy(CommandLine.arguments.contains("--service") ? .accessory : .regular); app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
