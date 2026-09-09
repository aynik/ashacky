import Foundation

/// Synthetic peer for cross-language transport checks; no app or device services.
@main enum ControlPeer {
    static func main() {
        let channel = ControlChannel(token: String(repeating: "x", count: 32))
        channel.write = { data, completion in
            do { try FileHandle.standardOutput.write(contentsOf: data); completion(true) }
            catch { completion(false) }
        }
        channel.failed = { exit(2) }
        channel.request = { service, payload, reply in
            DispatchQueue.main.asyncAfter(deadline: .now() + (service == "wifi" ? 0.1 : 0)) {
                reply(["ok": true, "service": service, "echo": payload])
            }
        }
        FileHandle.standardInput.readabilityHandler = { handle in
            let bytes = handle.availableData
            DispatchQueue.main.async {
                if bytes.isEmpty { channel.close(); exit(0) }
                channel.receive(bytes)
            }
        }
        dispatchMain()
    }
}
