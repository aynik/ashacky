import AVFoundation
import SystemConfiguration

/// Live camera stream for a same-user client in the active console session.
/// Nothing is recorded; each connection is bounded to thirty seconds.
final class CameraService: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let control = DispatchQueue(label: "local.linuxhost.camera.control")
    let frames = DispatchQueue(label: "local.linuxhost.camera.frames")
    let lock = NSLock()
    var processLock: ServiceProcessLock?
    var server: RawUnixServer?
    var session: AVCaptureSession?
    var client: Int32 = -1
    var activeOutput: AVCaptureOutput?
    var count: UInt32 = 0

    static func active() -> Bool { AshackyHostServices.active() }
    func start(directory: URL) throws {
        processLock = try ServiceProcessLock(path: directory.path + "/camera-workbench.lock")
        server = try RawUnixServer(path: directory.path + "/camera-workbench.sock") { [self] fd in
            var uid: uid_t = 0, gid: gid_t = 0
            guard getpeereid(fd, &uid, &gid) == 0, uid == getuid() else { close(fd); return }
            prepareSocket(fd, seconds: 2)
            control.async { self.start(fd) }
        }
    }
    func requestAuthorization() {
        guard Self.active() else { return }
        AVCaptureDevice.requestAccess(for: .video) { _ in }
    }
    func shutdown() { control.sync { self.stop() } }
    func message(_ text: String) { NSLog("%@", text) }
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
            message("Camera is streaming to the Linux virtual machine. It will stop within 30 seconds.")
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
}
