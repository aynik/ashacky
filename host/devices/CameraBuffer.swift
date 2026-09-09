import Foundation
import Darwin

@_silgen_name("ashacky_camera_map") func cameraMap(_ path: UnsafePointer<CChar>, _ writable: Int32) -> UnsafeMutableRawPointer?
@_silgen_name("ashacky_camera_unmap") func cameraUnmap(_ memory: UnsafeMutableRawPointer)
@_silgen_name("ashacky_camera_clear") func cameraClear(_ memory: UnsafeMutableRawPointer)
@_silgen_name("ashacky_camera_publish") func cameraPublish(_ memory: UnsafeMutableRawPointer, _ slot: UInt32,
    _ generation: UnsafePointer<UInt8>, _ sequence: UInt64, _ y: UnsafeRawPointer, _ yStride: Int,
    _ uv: UnsafeRawPointer, _ uvStride: Int) -> Int32

/// Condition-driven, bounded latest-frame queue. The leased slot is never reused
/// until ack; a slow reader drops queued frames without blocking AVFoundation.
final class CameraBuffer {
    let condition = NSCondition()
    let memory: UnsafeMutableRawPointer
    private var stream: UUID?
    private var generation = [UInt8]()
    private var sequence = 0
    private var queued: (slot: Int, sequence: Int)?
    private var leased: (slot: Int, sequence: Int)?
    init(path: String) throws {
        guard let memory = cameraMap(path, 1) else { throw IPCError.message("Camera shared memory unavailable") }
        self.memory = memory
        cameraClear(memory)
    }
    deinit { cameraClear(memory); cameraUnmap(memory) }
    func begin() -> String {
        condition.lock(); defer { condition.unlock() }
        cameraClear(memory)
        let id = UUID(); stream = id
        var bytes = id.uuid
        generation = withUnsafeBytes(of: &bytes) { Array($0) }
        sequence = 0; queued = nil; leased = nil
        condition.broadcast()
        return id.uuidString
    }
    func end() {
        condition.lock(); defer { condition.unlock() }
        stream = nil; queued = nil; leased = nil
        cameraClear(memory); condition.broadcast()
    }
    func publish(y: UnsafeRawPointer, yStride: Int, uv: UnsafeRawPointer, uvStride: Int) {
        condition.lock(); defer { condition.unlock() }
        guard stream != nil, sequence < Int(Int32.max) else { return }
        // Replace any queued frame; the guest is entitled only to its leased slot.
        let slot = (0..<3).first { $0 != leased?.slot && $0 != queued?.slot }!
        sequence += 1
        if cameraPublish(memory, UInt32(slot), generation, UInt64(sequence), y, yStride, uv, uvStride) == 0 {
            queued = (slot, sequence); condition.signal()
        }
    }
    func next(stream requested: String, ack: Int, timeout: TimeInterval = 1) throws -> [String: Any] {
        condition.lock(); defer { condition.unlock() }
        guard stream?.uuidString == requested,
              ack == (leased?.sequence ?? 0) else { throw IPCError.message("Invalid camera stream or acknowledgement") }
        leased = nil
        let deadline = Date(timeIntervalSinceNow: timeout)
        while queued == nil && stream?.uuidString == requested {
            if !condition.wait(until: deadline) { break }
        }
        guard stream?.uuidString == requested, let frame = queued else { throw IPCError.message("Camera stream stopped or stalled") }
        queued = nil; leased = frame
        return ["ok": true, "stream": requested, "slot": frame.slot, "sequence": frame.sequence,
                "width": 1280, "height": 720, "bytes": 1280 * 720 * 3 / 2, "format": "nv12"]
    }
}
