import Foundation
import Darwin

@_silgen_name("ashacky_camera_copy") func cameraCopy(_ memory: UnsafeMutableRawPointer, _ slot: UInt32,
    _ generation: UnsafePointer<UInt8>, _ sequence: UInt64, _ output: UnsafeMutableRawPointer, _ count: Int) -> Int32

@main struct CameraMemoryCheck {
    static func publish(_ buffer: CameraBuffer, _ value: UInt8) {
        let y = [UInt8](repeating: value, count: 1344 * 720)
        let uv = [UInt8](repeating: 128, count: 1344 * 360)
        y.withUnsafeBytes { y in uv.withUnsafeBytes { uv in
            buffer.publish(y: y.baseAddress!, yStride: 1344, uv: uv.baseAddress!, uvStride: 1344)
        } }
    }
    static func main() throws {
        let buffer = try CameraBuffer(path: CommandLine.arguments[1])
        if CommandLine.arguments.contains("--peer") {
            var count: UInt8 = 0
            while let line = readLine() {
                let request = try JSONSerialization.jsonObject(with: Data(line.utf8)) as! [String: Any]
                let reply: [String: Any]
                switch request["action"] as? String {
                case "camera-start":
                    reply = ["ok": true, "stream": buffer.begin(), "width": 1280, "height": 720,
                             "memoryBytes": 8 * 1024 * 1024, "format": "nv12"]
                case "camera-next":
                    count += 1; publish(buffer, count)
                    reply = try buffer.next(stream: request["stream"] as! String, ack: request["ack"] as! Int)
                default: buffer.end(); reply = ["ok": true]
                }
                let data = try JSONSerialization.data(withJSONObject: reply)
                print(String(data: data, encoding: .utf8)!); fflush(stdout)
            }
            return
        }
        let id = buffer.begin()
        publish(buffer, 90)
        let first = try buffer.next(stream: id, ack: 0)
        for _ in 0..<10 { publish(buffer, 180) }
        var bytes = UUID(uuidString: id)!.uuid
        let gen = withUnsafeBytes(of: &bytes) { Array($0) }
        let pixels = UnsafeMutableRawPointer.allocate(byteCount: 1280 * 720 * 3 / 2, alignment: 16)
        defer { pixels.deallocate() }
        precondition(cameraCopy(buffer.memory, UInt32(first["slot"] as! Int), gen, 1, pixels, 1280 * 720 * 3 / 2) == 0)
        precondition(pixels.load(as: UInt8.self) == 90, "Leased frame overwritten")
        let latest = try buffer.next(stream: id, ack: 1)
        precondition(latest["sequence"] as? Int == 11, "Slow reader did not receive newest frame")
        do { _ = try buffer.next(stream: id, ack: 1); fatalError("Duplicate ack accepted") } catch {}
        buffer.end()
        precondition(cameraCopy(buffer.memory, UInt32(first["slot"] as! Int), gen, 1, pixels, 1280 * 720 * 3 / 2) == -2)
        let fresh = buffer.begin(); precondition(fresh != id)
        do { _ = try buffer.next(stream: id, ack: 0); fatalError("Old stream accepted") } catch {}
        DispatchQueue.global().async { publish(buffer, 70) }
        let awakened = try buffer.next(stream: fresh, ack: 0)
        precondition(awakened["sequence"] as? Int == 1)
        let before = Date()
        do { _ = try buffer.next(stream: fresh, ack: 1, timeout: 0.02); fatalError("Stall accepted") } catch {}
        precondition(Date().timeIntervalSince(before) < 0.5)
        buffer.end()
        print("Camera slot ownership, latest-frame queue, revocation and bounded event wait passed")
    }
}
