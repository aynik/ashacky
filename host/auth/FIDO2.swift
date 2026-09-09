// SPDX-License-Identifier: MIT
import Foundation
import CryptoKit
import LocalAuthentication
import Security

struct FIDOCredential: Codable {
    let id: Data
    let rp: String
    let user: Data
    let name: String
    let displayName: String
    let discoverable: Bool
    let protection: Int
    let publicKey: Data
    // CryptoKit's device-bound encrypted representation, never an exported private key.
    let wrappedKey: Data
}

/// One request's authorization. Cancellation invalidates the actual native prompt.
final class FIDOTransaction {
    let context = LAContext()
    private let lock = NSLock()
    private var stopped = false
    let allowed: () -> Bool
    init(allowed: @escaping () -> Bool) {
        self.allowed = allowed
        context.localizedFallbackTitle = "Use Mac Password"
        context.touchIDAuthenticationAllowableReuseDuration = 0
    }
    func cancel() { lock.lock(); stopped = true; lock.unlock(); context.invalidate() }
    var cancelled: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
    func check() throws {
        lock.lock(); let cancelled = stopped; lock.unlock()
        if cancelled { throw FIDOError.status(0x2d) }
        if !allowed() { throw FIDOError.status(0x27) }
    }
}

protocol FIDOKeyStore {
    func credentials() throws -> [FIDOCredential]
    func generate(rp: String, user: Data, name: String, displayName: String,
                  discoverable: Bool, protection: Int, transaction: FIDOTransaction) throws -> FIDOCredential
    func sign(_ credential: FIDOCredential, data: Data, transaction: FIDOTransaction) throws -> Data
    func save(_ credential: FIDOCredential) throws
}

/// Small CTAP2.0 authenticator. All RP binding and signed bytes are constructed on
/// the host; the guest cannot request arbitrary Secure Enclave signatures.
final class FIDO2Authenticator {
    static let maximumMessage = 1024
    static let maximumCredentials = 256
    let store: FIDOKeyStore
    private var next: (channel: String, deadline: Date, responses: [CBOR])?
    init(store: FIDOKeyStore) { self.store = store }
    func clear() { next = nil }

    static var info: CBOR { .map([
        .int(1): .array([.text("FIDO_2_0")]),
        .int(2): .array([.text("credProtect")]),
        .int(3): .bytes(Data(repeating: 0, count: 16)),
        .int(4): .map([.text("rk"): .bool(true), .text("up"): .bool(true),
                      .text("uv"): .bool(true), .text("plat"): .bool(false), .text("alwaysUv"): .bool(true)]),
        .int(5): .int(Int64(maximumMessage)),
    ]) }

    func process(_ message: Data, channel: String, transaction: FIDOTransaction) -> Data {
        do {
            try transaction.check()
            guard let command = message.first, message.count <= Self.maximumMessage else { throw FIDOError.status(0x03) }
            if command != 0x08 { next = nil }
            let result: CBOR
            switch command {
            case 0x04:
                guard message.count == 1 else { throw FIDOError.status(0x03) }
                result = Self.info
            case 0x01, 0x02:
                let request = try CBOR.decode(Data(message.dropFirst()))
                guard request.fields != nil else { throw FIDOError.status(0x11) }
                result = command == 1 ? try make(request, transaction) : try assertion(request, channel, transaction)
            case 0x08:
                guard message.count == 1 else { throw FIDOError.status(0x03) }
                guard var batch = next, batch.channel == channel, Date() < batch.deadline,
                      !batch.responses.isEmpty else { next = nil; throw FIDOError.status(0x30) }
                result = batch.responses.removeFirst()
                next = batch.responses.isEmpty ? nil : batch
            default: throw FIDOError.status(0x01)
            }
            try transaction.check()
            let response = Data([0]) + result.encoded()
            guard response.count <= Self.maximumMessage else { throw FIDOError.status(0x15) }
            return response
        } catch FIDOError.status(let code) { next = nil; return Data([code]) }
        catch { next = nil; return Data([0x7f]) }
    }

    private func hash(_ value: CBOR?) throws -> Data {
        guard let value else { throw FIDOError.status(0x14) }
        guard let data = value.data, data.count == 32 else { throw FIDOError.status(0x02) }
        return data
    }
    private func rp(_ value: CBOR?) throws -> String {
        guard let value else { throw FIDOError.status(0x14) }
        guard let text = value.string, !text.isEmpty, text.utf8.count <= 253,
              text.unicodeScalars.allSatisfy({ $0.isASCII && (CharacterSet.alphanumerics.contains($0) || ".-".unicodeScalars.contains($0)) }),
              !text.hasPrefix("."), !text.hasSuffix("."), !text.contains("..") else { throw FIDOError.status(0x02) }
        return text
    }
    private func label(_ value: CBOR?) throws -> String {
        guard let value else { return "" }
        guard let text = value.string, text.utf8.count <= 128,
              !text.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) || CharacterSet.illegalCharacters.contains($0) })
        else { throw FIDOError.status(0x02) }
        return text
    }
    private func descriptors(_ value: CBOR?) throws -> [Data]? {
        guard let value else { return nil }
        guard let list = value.values, list.count <= 64 else { throw FIDOError.status(0x15) }
        return try list.compactMap {
            guard $0.fields != nil, let type = $0["type"]?.string,
                  let id = $0["id"]?.data, !id.isEmpty, id.count <= 1024 else { throw FIDOError.status(0x02) }
            return type == "public-key" ? id : nil
        }
    }
    private func options(_ value: CBOR?, make: Bool) throws -> [String: Bool] {
        guard let value else { return [:] }
        guard let fields = value.fields else { throw FIDOError.status(0x11) }
        var result: [String: Bool] = [:]
        for (key, value) in fields {
            guard case .text(let name) = key, let flag = value.boolean else { throw FIDOError.status(0x11) }
            if make && name == "up" { throw FIDOError.status(0x2c) }
            if !["rk", "uv", "up"].contains(name) && flag { throw FIDOError.status(0x2b) }
            if !make && name == "rk" { throw FIDOError.status(0x2b) }
            result[name] = flag
        }
        return result
    }
    private func extensions(_ value: CBOR?) throws -> Int? {
        guard let value else { return nil }
        guard value.fields != nil else { throw FIDOError.status(0x11) }
        if let cp = value["credProtect"] {
            guard let level = cp.integer, (1...3).contains(level) else { throw FIDOError.status(0x02) }
            return Int(level)
        }
        return nil
    }
    private func authData(rp: String, flags: UInt8) -> Data {
        // Zero counter explicitly means unsupported. No clonable guest-side counter.
        Data(SHA256.hash(data: Data(rp.utf8))) + Data([flags, 0, 0, 0, 0])
    }
    private func publicCOSE(_ credential: FIDOCredential) throws -> CBOR {
        let key = credential.publicKey
        guard key.count == 65, key.first == 4 else { throw FIDOError.status(0x7f) }
        return .map([.int(1): .int(2), .int(3): .int(-7), .int(-1): .int(1),
                     .int(-2): .bytes(key.subdata(in: 1..<33)), .int(-3): .bytes(key.subdata(in: 33..<65))])
    }

    private func make(_ request: CBOR, _ tx: FIDOTransaction) throws -> CBOR {
        let clientHash = try hash(request[1]), relyingParty = try rp(request[2]?["id"])
        guard let user = request[3]?["id"]?.data, (1...64).contains(user.count),
              let algorithms = request[4]?.values, !algorithms.isEmpty, algorithms.count <= 64 else { throw FIDOError.status(0x14) }
        let opts = try options(request[7], make: true)
        let cp = try extensions(request[6])
        guard request[8] == nil, request[9] == nil, request[10] == nil else { throw FIDOError.status(0x2b) }
        guard algorithms.contains(where: { $0["type"]?.string == "public-key" && $0["alg"]?.integer == -7 })
        else { throw FIDOError.status(0x26) }
        let excluded = try descriptors(request[5]) ?? []
        let existing = try store.credentials()
        if let match = existing.first(where: { $0.rp == relyingParty && excluded.contains($0.id) }) {
            // Existence is disclosed only after the protected key authorizes use.
            tx.context.localizedReason = "check an existing passkey for \(relyingParty)"
            _ = try store.sign(match, data: authData(rp: relyingParty, flags: 5) + clientHash, transaction: tx)
            throw FIDOError.status(0x19)
        }
        let replacing = opts["rk"] == true && existing.contains {
            $0.discoverable && $0.rp == relyingParty && $0.user == user
        }
        guard replacing || existing.count < Self.maximumCredentials else { throw FIDOError.status(0x28) }
        tx.context.localizedReason = "create a passkey for \(relyingParty)"
        let credential = try store.generate(rp: relyingParty, user: user,
            name: label(request[3]?["name"]), displayName: label(request[3]?["displayName"]),
            discoverable: opts["rk"] ?? false, protection: max(2, cp ?? 2), transaction: tx)
        var data = authData(rp: relyingParty, flags: cp != nil ? 0xc5 : 0x45)
        data += Data(repeating: 0, count: 16) // no manufacturer attestation / AAGUID claim
        data += Data([0, UInt8(credential.id.count)]) + credential.id
        data += try publicCOSE(credential).encoded()
        if cp != nil { data += CBOR.map([.text("credProtect"): .int(Int64(credential.protection))]).encoded() }
        let signature = try store.sign(credential, data: data + clientHash, transaction: tx)
        try tx.check()
        try store.save(credential)
        return .map([.int(1): .text("packed"), .int(2): .bytes(data),
                     .int(3): .map([.text("alg"): .int(-7), .text("sig"): .bytes(signature)])])
    }

    private func assertion(_ request: CBOR, _ channel: String, _ tx: FIDOTransaction) throws -> CBOR {
        let relyingParty = try rp(request[1]), clientHash = try hash(request[2])
        let allowed = try descriptors(request[3])
        _ = try extensions(request[4])
        let opts = try options(request[5], make: false)
        guard request[6] == nil, request[7] == nil else { throw FIDOError.status(0x2b) }
        let matches = try store.credentials().filter { credential in
            credential.rp == relyingParty && (allowed.map { $0.contains(credential.id) } ?? credential.discoverable)
        }
        guard !matches.isEmpty else { throw FIDOError.status(0x2e) }
        if opts["up"] == false && opts["uv"] != true {
            // CTAP 7.2.3 permits a syntactically valid, deliberately invalid
            // ECDSA (1, 1) signature for client pre-flight. No protected key is
            // used, no user information is returned, and UP/UV remain zero.
            // Require a known ID; credProtect=3 must not disclose even existence.
            guard allowed != nil, let credential = matches.first(where: { $0.protection < 3 })
            else { throw FIDOError.status(0x2e) }
            return .map([
                .int(1): .map([.text("type"): .text("public-key"), .text("id"): .bytes(credential.id)]),
                .int(2): .bytes(authData(rp: relyingParty, flags: 0)),
                .int(3): .bytes(Data([0x30, 6, 2, 1, 1, 2, 1, 1])),
            ])
        }
        tx.context.localizedReason = "use a passkey for \(relyingParty)"
        var replies: [CBOR] = []
        for credential in matches {
            try tx.check()
            let data = authData(rp: relyingParty, flags: opts["up"] == false ? 4 : 5)
            let signature = try store.sign(credential, data: data + clientHash, transaction: tx)
            var fields: [CBORKey: CBOR] = [
                .int(1): .map([.text("type"): .text("public-key"), .text("id"): .bytes(credential.id)]),
                .int(2): .bytes(data), .int(3): .bytes(signature),
                .int(4): .map([.text("id"): .bytes(credential.user), .text("name"): .text(credential.name),
                              .text("displayName"): .text(credential.displayName)]),
            ]
            if replies.isEmpty && matches.count > 1 { fields[.int(5)] = .int(Int64(matches.count)) }
            replies.append(.map(fields))
        }
        let first = replies.removeFirst()
        if !replies.isEmpty { next = (channel, Date().addingTimeInterval(30), replies) }
        return first
    }
}
