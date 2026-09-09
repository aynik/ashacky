// SPDX-License-Identifier: MIT
import AppKit
import CryptoKit
import Darwin
import Foundation
import LocalAuthentication
import Security

final class SecureEnclaveFIDOStore: FIDOKeyStore {
    private let directory: URL
    private let state: URL
    init(directory: URL) throws {
        self.directory = directory
        self.state = directory.appendingPathComponent("credentials.json")
        // Foundation's non-recursive createDirectory throws when a directory
        // already exists on macOS. Reopening a store must validate that directory,
        // not recreate it or relax its permissions.
        guard mkdir(directory.path, 0o700) == 0 || errno == EEXIST else { throw FIDOError.status(0x7f) }
        var info = stat()
        guard lstat(directory.path, &info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == S_IFDIR, info.st_mode & 0o077 == 0 else { throw FIDOError.status(0x7f) }
    }
    func credentials() throws -> [FIDOCredential] {
        let fd = open(state.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        if fd < 0 && errno == ENOENT { return [] }
        guard fd >= 0 else { throw FIDOError.status(0x7f) }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_uid == getuid(), info.st_mode & 0o077 == 0,
              info.st_mode & S_IFMT == S_IFREG, info.st_nlink == 1, (1...2097152).contains(info.st_size)
        else { throw FIDOError.status(0x7f) }
        var bytes = Data(), buffer = [UInt8](repeating: 0, count: 8192)
        while true {
            let count = Darwin.read(fd, &buffer, buffer.count)
            guard count >= 0, bytes.count + count <= 2097152 else { throw FIDOError.status(0x7f) }
            if count == 0 { break }; bytes.append(contentsOf: buffer[0..<count])
        }
        let records = try JSONDecoder().decode([FIDOCredential].self, from: bytes)
        guard records.count <= FIDO2Authenticator.maximumCredentials,
              Set(records.map(\.id)).count == records.count,
              records.allSatisfy({ $0.id.count == 32 && $0.publicKey.count == 65 &&
                  (1...64).contains($0.user.count) && $0.rp.utf8.count <= 253 &&
                  (2...3).contains($0.protection) &&
                  $0.name.utf8.count <= 128 && $0.displayName.utf8.count <= 128 &&
                  (1...4096).contains($0.wrappedKey.count) }) else { throw FIDOError.status(0x7f) }
        return records
    }
    func generate(rp: String, user: Data, name: String, displayName: String,
                  discoverable: Bool, protection: Int, transaction: FIDOTransaction) throws -> FIDOCredential {
        try transaction.check()
        guard SecureEnclave.isAvailable,
              let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
                  [.privateKeyUsage, .userPresence], nil) else { throw FIDOError.status(0x7f) }
        let key = try SecureEnclave.P256.Signing.PrivateKey(compactRepresentable: false,
            accessControl: access, authenticationContext: transaction.context)
        var identifier = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, identifier.count, &identifier) == errSecSuccess else { throw FIDOError.status(0x7f) }
        return FIDOCredential(id: Data(identifier), rp: rp, user: user, name: name, displayName: displayName,
            discoverable: discoverable, protection: protection, publicKey: key.publicKey.x963Representation, wrappedKey: key.dataRepresentation)
    }
    func sign(_ credential: FIDOCredential, data: Data, transaction: FIDOTransaction) throws -> Data {
        try transaction.check()
        let key = try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: credential.wrappedKey,
                                                          authenticationContext: transaction.context)
        guard key.publicKey.x963Representation == credential.publicKey else { throw FIDOError.status(0x7f) }
        do {
            let signature = try key.signature(for: data).derRepresentation
            try transaction.check()
            return signature
        } catch {
            try transaction.check()
            // Native cancellation/denial does not produce a successful assertion.
            throw FIDOError.status(0x27)
        }
    }
    func save(_ credential: FIDOCredential) throws {
        var records = try credentials()
        // Resident credentials replace the same RP/user pair, as CTAP requires.
        if credential.discoverable {
            records.removeAll { $0.discoverable && $0.rp == credential.rp && $0.user == credential.user }
        }
        guard records.count < FIDO2Authenticator.maximumCredentials else { throw FIDOError.status(0x28) }
        records.append(credential)
        let data = try JSONEncoder().encode(records)
        let temporary = directory.appendingPathComponent(".write-" + UUID().uuidString)
        let fd = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw FIDOError.status(0x7f) }
        defer { close(fd); unlink(temporary.path) }
        try data.withUnsafeBytes { bytes in
            var offset = 0
            while offset < bytes.count {
                let count = Darwin.write(fd, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
                guard count > 0 else { throw FIDOError.status(0x7f) }; offset += count
            }
        }
        guard fsync(fd) == 0, rename(temporary.path, state.path) == 0 else { throw FIDOError.status(0x7f) }
        let parent = open(directory.path, O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        if parent >= 0 { _ = fsync(parent); close(parent) }
    }
}

/// Called on the app main loop through the already authenticated private port.
/// No standalone app, TCP listener, generic signing RPC or new TCC identity.
final class FIDO2Service {
    private let core: FIDO2Authenticator
    private let queue = DispatchQueue(label: "local.ashacky.fido2", qos: .userInitiated)
    private var current: (id: String, transaction: FIDOTransaction)?
    private var generation = UUID()
    private var observers: [NSObjectProtocol] = []
    private var distributed: NSObjectProtocol?

    init(directory: URL, namespace: String) throws {
        let parent = directory.appendingPathComponent("fido2")
        // Validate both private levels even when the namespace is new.
        _ = try SecureEnclaveFIDOStore(directory: parent)
        let name = SHA256.hash(data: Data(namespace.utf8)).map { String(format: "%02x", $0) }.joined()
        core = FIDO2Authenticator(store: try SecureEnclaveFIDOStore(directory: parent.appendingPathComponent(name)))
        for event in [NSWorkspace.willSleepNotification, NSWorkspace.sessionDidResignActiveNotification] {
            observers.append(NSWorkspace.shared.notificationCenter.addObserver(forName: event, object: nil, queue: .main) { [weak self] _ in self?.cancelAll() })
        }
        distributed = DistributedNotificationCenter.default().addObserver(forName: NSNotification.Name("com.apple.screenIsLocked"),
            object: nil, queue: .main) { [weak self] _ in self?.cancelAll() }
    }
    func cancelAll() {
        generation = UUID()
        current?.transaction.cancel()
        queue.async { [core] in core.clear() }
    }
    func request(_ request: [String: Any], reply: @escaping ([String: Any]) -> Void) {
        guard let id = request["id"] as? String, UUID(uuidString: id) != nil else { reply(["ok": false]); return }
        if request["action"] as? String == "cancel" {
            guard Set(request.keys) == ["action", "id"] else { reply(["ok": false]); return }
            if current?.id == id { current?.transaction.cancel() }
            queue.async { [core] in core.clear() }
            reply(["ok": true]); return
        }
        guard Set(request.keys) == ["action", "id", "channel", "data"], request["action"] as? String == "request",
              let channel = request["channel"] as? String, channel.utf8.count == 8,
              channel.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              let encoded = request["data"] as? String, encoded.utf8.count <= 1368,
              let data = Data(base64Encoded: encoded), !data.isEmpty, data.count <= FIDO2Authenticator.maximumMessage
        else { reply(["ok": false]); return }
        // Phase/status only: never log RP, user, credential IDs or request bytes.
        let command = data.first!
        NSLog("FIDO2 request command=%02x", Int(command))
        guard current == nil else { reply(["ok": true, "data": Data([0x21]).base64EncodedString()]); return }
        guard Control.active(), !Control.sleeping else { reply(["ok": false]); return }
        // Discovery is harmless in the background; protected actions begin only
        // from the foreground VM. The native authentication UI may then take focus.
        guard data.first == 4 || NSApp.isActive else { reply(["ok": true, "data": Data([0x27]).base64EncodedString()]); return }
        let expected = generation
        let transaction = FIDOTransaction {
            DispatchQueue.main.sync { Control.active() && !Control.sleeping }
        }
        current = (id, transaction)
        let timeout = DispatchWorkItem { transaction.cancel() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 55, execute: timeout)
        queue.async { [self] in
            let result = core.process(data, channel: channel, transaction: transaction)
            transaction.context.invalidate()
            DispatchQueue.main.async { [self] in
                timeout.cancel()
                current = nil
                let valid = generation == expected && !transaction.cancelled && Control.active() && !Control.sleeping
                NSLog("FIDO2 completed command=%02x status=%02x", Int(command), Int(valid ? (result.first ?? 0x7f) : 0x2d))
                reply(["ok": true, "data": (valid ? result : Data([0x2d])).base64EncodedString()])
            }
        }
    }
    deinit {
        current?.transaction.cancel()
        for observer in observers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        if let distributed { DistributedNotificationCenter.default().removeObserver(distributed) }
    }
}
