import Foundation

func checkControlChannel() {
    let token = String(repeating: "x", count: 32)
    let channel = ControlChannel(token: token)
    var sent: [[String: Any]] = []
    var completions: [(Bool) -> Void] = []
    var failures = 0
    var calls = 0
    var replies: [([String: Any]) -> Void] = []
    channel.failed = { failures += 1 }
    channel.write = { data, completion in
        sent.append(try! JSONSerialization.jsonObject(with: data) as! [String: Any])
        completions.append(completion)
    }
    channel.request = { _, _, reply in calls += 1; replies.append(reply) }
    func spin() { RunLoop.main.run(until: Date().addingTimeInterval(0.01)) }
    func finish() { completions.removeFirst()(true); spin() }
    func send(_ fields: [String: Any], fragmented: Bool = false) {
        var value = fields; value["version"] = 1
        var data = try! JSONSerialization.data(withJSONObject: value); data.append(10)
        if fragmented { for byte in data { channel.receive(Data([byte])) } }
        else { channel.receive(data) }
    }
    func hello() -> String {
        send(["type": "hello", "token": token, "nonce": UUID().uuidString], fragmented: true)
        let session = channel.session!
        precondition(sent.last!["type"] as? String == "welcome")
        finish()
        return session
    }
    func request(_ id: Int, _ session: String, action: String = "status") {
        send(["type": "request", "session": session, "id": id, "service": "host", "payload": ["action": action]])
    }
    send(["type": "hello", "token": "wrong", "nonce": UUID().uuidString])
    precondition(failures == 1 && channel.session == nil)
    let first = hello()
    request(1, first, action: "vm-stopped")
    precondition(calls == 0 && (sent.last!["payload"] as! [String: Any])["ok"] as? Bool == false)
    finish()
    request(2, first); request(3, first)
    precondition(calls == 2)
    replies[1](["ok": true]); spin()
    precondition(sent.last!["id"] as? Int == 3, "Slow requests must not block an independent response")
    finish()
    // A delayed reply cannot cross a port reopen.
    channel.close()
    let second = hello()
    let before = sent.count
    replies[0](["ok": true]); spin()
    precondition(sent.count == before && first != second)
    request(4, first)
    precondition(failures == 2 && channel.session == nil)
    let third = hello()
    let start = replies.count
    for id in 1...17 { request(id, third) }
    precondition(replies.count == start + 16)
    precondition((sent.last!["payload"] as! [String: Any])["ok"] as? Bool == false)
    finish()
    channel.close()
    _ = hello()
    request(1, channel.session!)
    precondition(replies.count == start + 16, "Reconnect must not reset outstanding worker budget")
    finish()
    for reply in replies[start...] { reply(["ok": true]) }; spin()
    channel.close()
    _ = hello()
    channel.receive(Data(repeating: 65, count: ControlChannel.maximumFrame + 1))
    precondition(failures == 3 && channel.session == nil)
    _ = hello()
    send(["type": "request", "session": channel.session!, "id": 0, "service": "host", "payload": [:]])
    precondition(failures == 4)
    channel.receive(Data("{\"version\":true,\"type\":\"hello\"}\n".utf8))
    precondition(failures == 5)
    channel.close()
    print("Control framing, authentication, concurrency, bounds and reconnect checks passed")
}
