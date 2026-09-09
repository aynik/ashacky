// SPDX-License-Identifier: MIT
// Test-only software signer. Never linked into Ashacky or written to disk.
import Foundation
import CryptoKit

final class FixtureFIDOStore: FIDOKeyStore {
    var records: [FIDOCredential] = []
    var keys: [Data: P256.Signing.PrivateKey] = [:]
    var signatures = 0
    func credentials() throws -> [FIDOCredential] { records }
    func generate(rp: String, user: Data, name: String, displayName: String,
                  discoverable: Bool, protection: Int, transaction: FIDOTransaction) throws -> FIDOCredential {
        let key = P256.Signing.PrivateKey()
        let id = Data(SHA256.hash(data: key.publicKey.x963Representation))
        keys[id] = key
        return FIDOCredential(id: id, rp: rp, user: user, name: name, displayName: displayName,
            discoverable: discoverable, protection: protection, publicKey: key.publicKey.x963Representation, wrappedKey: Data())
    }
    func sign(_ credential: FIDOCredential, data: Data, transaction: FIDOTransaction) throws -> Data {
        try transaction.check()
        signatures += 1
        return try keys[credential.id]!.signature(for: data).derRepresentation
    }
    func save(_ credential: FIDOCredential) throws {
        if credential.discoverable { records.removeAll { $0.rp == credential.rp && $0.user == credential.user } }
        records.append(credential)
    }
}

@main enum FIDOPeer {
    static func main() throws {
        let store = FixtureFIDOStore()
        let core = FIDO2Authenticator(store: store)
        while let line = readLine() {
            let value = try JSONSerialization.jsonObject(with: Data(line.utf8)) as! [String: Any]
            let allowed = value["allowed"] as? Bool ?? true
            let tx = FIDOTransaction { allowed }
            if value["cancelled"] as? Bool == true { tx.cancel() }
            let payload = Data(base64Encoded: value["data"] as? String ?? "") ?? Data()
            let result = core.process(payload, channel: value["channel"] as? String ?? "00000001", transaction: tx)
            let response: [String: Any] = ["fixture": "ashacky-fido2-software-test-only", "data": result.base64EncodedString(),
                "stored": store.records.count, "signatures": store.signatures]
            let output = try JSONSerialization.data(withJSONObject: response)
            FileHandle.standardOutput.write(output + Data([10]))
        }
    }
}
