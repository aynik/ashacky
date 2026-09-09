import Foundation
import CoreFoundation

/// Main-run-loop protocol engine for the private SPICE port. No network or shell RPC.
final class ControlChannel {
    static let maximumFrame = 32768
    static let maximumQueue = 262144
    let token: String
    var write: ((Data, @escaping (Bool) -> Void) -> Void)?
    var failed: (() -> Void)?
    var request: ((String, [String: Any], @escaping ([String: Any]) -> Void) -> Void)?
    private(set) var session: String?
    private var generation = UUID()
    private var buffer = Data()
    private var queue: [Data] = []
    private var queuedBytes = 0
    private var writing = false
    private var active = Set<Int>()
    private var outstanding = 0
    private var lastRequest = 0

    init(token: String) { self.token = token }

    private func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              !["f", "d"].contains(String(cString: number.objCType)),
              number.stringValue == String(number.int64Value) else { return nil }
        return Int(exactly: number.int64Value)
    }

    func close() {
        generation = UUID(); session = nil; buffer.removeAll()
        queue.removeAll(); queuedBytes = 0; writing = false
        active.removeAll(); lastRequest = 0
    }

    private func fail() { close(); failed?() }

    func receive(_ data: Data) {
        // Process fragments without ever accumulating an unbounded frame.
        for byte in data {
            if byte == 10 {
                let line = buffer; buffer.removeAll(keepingCapacity: true)
                guard let value = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any],
                      integer(value["version"]) == 1 else { fail(); return }
                guard handle(value) else { fail(); return }
            } else {
                guard buffer.count < Self.maximumFrame else { fail(); return }
                buffer.append(byte)
            }
        }
    }

    private func enqueue(_ fields: [String: Any]) {
        var value = fields; value["version"] = 1
        guard var bytes = try? JSONSerialization.data(withJSONObject: value),
              bytes.count <= Self.maximumFrame else { fail(); return }
        bytes.append(10)
        guard queuedBytes + bytes.count <= Self.maximumQueue else { fail(); return }
        queue.append(bytes); queuedBytes += bytes.count; drain()
    }

    private func drain() {
        guard !writing, let bytes = queue.first, let write else { return }
        writing = true
        let expected = generation
        let timeout = DispatchWorkItem { [weak self] in
            guard let self, self.generation == expected, self.writing else { return }
            self.fail()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 10, execute: timeout)
        write(bytes) { [weak self] success in
            DispatchQueue.main.async {
                timeout.cancel()
                guard let self, self.generation == expected else { return }
                guard success else { self.fail(); return }
                self.queue.removeFirst(); self.queuedBytes -= bytes.count
                self.writing = false; self.drain()
            }
        }
    }

    private func handle(_ value: [String: Any]) -> Bool {
        if session == nil {
            guard value["type"] as? String == "hello", value["token"] as? String == token,
                  let nonce = value["nonce"] as? String, UUID(uuidString: nonce) != nil else { return false }
            session = UUID().uuidString
            enqueue(["type": "welcome", "nonce": nonce, "session": session!, "token": token])
            NSLog("Private control channel authenticated")
            return true
        }
        guard value["session"] as? String == session else { return false }
        switch value["type"] as? String {
        case "request":
            guard let id = integer(value["id"]), id > lastRequest,
                  let service = value["service"] as? String, ["host", "wifi", "bluetooth", "camera"].contains(service),
                  let payload = value["payload"] as? [String: Any], let request else { return false }
            lastRequest = id
            guard payload["action"] as? String != "vm-stopped" else {
                reply(id, ["ok": false, "error": "Supervisor endpoint required"]); return true
            }
            guard outstanding < 16 else {
                reply(id, ["ok": false, "error": "Control channel busy"]); return true
            }
            active.insert(id)
            outstanding += 1
            let expected = generation
            request(service, payload) { [weak self] result in
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.outstanding -= 1
                    guard self.generation == expected, self.active.remove(id) != nil else { return }
                    self.reply(id, result)
                }
            }
        default: return false
        }
        return true
    }

    private func reply(_ id: Int, _ payload: [String: Any]) {
        guard let session else { return }
        enqueue(["type": "response", "session": session, "id": id, "payload": payload])
    }

}
