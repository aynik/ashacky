import AppKit
import AVFoundation
import CoreFoundation

/// Demand-controlled camera. Only descriptors cross private RPC; pixels live in
/// a separate shared PCI mapping. No SSH, file recording or idle capture.
final class CameraService: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let control = DispatchQueue(label: "local.ashacky.camera.control")
    let frames = DispatchQueue(label: "local.ashacky.camera.frames")
    let lock = NSLock()
    var processLock: ServiceProcessLock?
    var server: UnixServer?
    var buffer: CameraBuffer?
    var session: AVCaptureSession?
    var activeOutput: AVCaptureOutput?
    var stream: String?
    var lease: DispatchSourceTimer?
    var observers = [NSObjectProtocol]()
    var count = 0

    static func active() -> Bool { AshackyHostServices.active() }
    func start(directory: URL) throws {
        processLock = try ServiceProcessLock(path: directory.path + "/camera-workbench.lock")
        buffer = try CameraBuffer(path: directory.appendingPathComponent("runtime/camera-frames.bin").path)
        server = try UnixServer(path: directory.path + "/camera-workbench.sock") { [self] fd, request in
            var uid: uid_t = 0, gid: gid_t = 0
            guard getpeereid(fd, &uid, &gid) == 0, uid == getuid(), Self.active() else {
                return ["ok": false, "error": "Camera requires the active console user"]
            }
            return control.sync {
                do { return try self.request(request) }
                catch { return ["ok": false, "error": String(describing: error)] }
            }
        }
        for name in [NSWorkspace.willSleepNotification, NSWorkspace.sessionDidResignActiveNotification] {
            observers.append(NSWorkspace.shared.notificationCenter.addObserver(forName: name, object: nil, queue: nil) { [weak self] _ in
                guard let self else { return }
                self.control.async { self.stop() }
            })
        }
    }
    func shutdown() {
        for observer in observers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        observers.removeAll()
        control.sync { self.stop() }
    }
    func renew() {
        if let lease { lease.schedule(deadline: .now() + 5); return }
        let timer = DispatchSource.makeTimerSource(queue: control)
        timer.schedule(deadline: .now() + 5)
        timer.setEventHandler { [weak self] in self?.stop() }
        lease = timer; timer.resume()
    }
    func request(_ value: [String: Any]) throws -> [String: Any] {
        guard Self.active(), AVCaptureDevice.authorizationStatus(for: .video) == .authorized else {
            stop(); throw IPCError.message("Camera permission or active session unavailable")
        }
        if value["action"] as? String == "camera-start", value.count == 1 {
            guard session == nil else { throw IPCError.message("Camera already in use") }
            let id = try begin()
            return ["ok": true, "stream": id, "width": 1280, "height": 720, "format": "nv12", "memoryBytes": 8 * 1024 * 1024]
        }
        guard let id = value["stream"] as? String, id == stream else { throw IPCError.message("Stale camera stream") }
        if value["action"] as? String == "camera-stop", value.count == 2 {
            stop(); return ["ok": true]
        }
        guard value["action"] as? String == "camera-next", value.count == 3,
              let ack = value["ack"] as? NSNumber, CFGetTypeID(ack) != CFBooleanGetTypeID(),
              !["f", "d"].contains(String(cString: ack.objCType)),
              ack.int64Value >= 0, ack.int64Value <= Int32.max else { throw IPCError.message("Invalid camera request") }
        do {
            let reply = try buffer!.next(stream: id, ack: ack.intValue)
            renew(); return reply
        } catch { stop(); throw error }
    }
    func begin() throws -> String {
        let discovery = AVCaptureDevice.DiscoverySession(deviceTypes: [.builtInWideAngleCamera], mediaType: .video, position: .unspecified)
        guard let camera = discovery.devices.first else { throw IPCError.message("Built-in camera unavailable") }
        let capture = AVCaptureSession()
        capture.beginConfiguration()
        guard capture.canSetSessionPreset(.hd1280x720) else { throw IPCError.message("Camera needs 1280x720 capture support") }
        capture.sessionPreset = .hd1280x720
        let input = try AVCaptureDeviceInput(device: camera)
        guard capture.canAddInput(input) else { throw IPCError.message("Camera input unavailable") }
        capture.addInput(input)
        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange]
        output.setSampleBufferDelegate(self, queue: frames)
        guard capture.canAddOutput(output) else { throw IPCError.message("Camera output unavailable") }
        capture.addOutput(output); capture.commitConfiguration()
        let id = buffer!.begin(); stream = id; session = capture
        lock.lock(); activeOutput = output; count = 0; lock.unlock()
        capture.startRunning(); renew()
        NSLog("Camera shared-memory capture started")
        return id
    }
    func stop(expectedOutput: AVCaptureOutput? = nil) {
        lock.lock()
        if let expectedOutput, activeOutput !== expectedOutput { lock.unlock(); return }
        let wasActive = activeOutput != nil, sent = count
        activeOutput = nil; lock.unlock()
        lease?.cancel(); lease = nil
        session?.stopRunning(); session = nil; stream = nil
        buffer?.end()
        if wasActive { NSLog("Camera stopped after %d shared frames; no recording was saved", sent) }
    }
    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer, from connection: AVCaptureConnection) {
        lock.lock(); defer { lock.unlock() }
        guard activeOutput === output else { return }
        guard Self.active(), let pixel = CMSampleBufferGetImageBuffer(sample),
              CVPixelBufferGetPixelFormatType(pixel) == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
              CVPixelBufferGetPlaneCount(pixel) == 2,
              CVPixelBufferGetWidth(pixel) == 1280, CVPixelBufferGetHeight(pixel) == 720 else {
            control.async { self.stop(expectedOutput: output) }; return
        }
        guard CVPixelBufferLockBaseAddress(pixel, .readOnly) == kCVReturnSuccess else { return }
        defer { CVPixelBufferUnlockBaseAddress(pixel, .readOnly) }
        guard let y = CVPixelBufferGetBaseAddressOfPlane(pixel, 0), let uv = CVPixelBufferGetBaseAddressOfPlane(pixel, 1) else { return }
        buffer?.publish(y: y, yStride: CVPixelBufferGetBytesPerRowOfPlane(pixel, 0), uv: uv, uvStride: CVPixelBufferGetBytesPerRowOfPlane(pixel, 1))
        count += 1
    }
}
