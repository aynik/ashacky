// SPDX-License-Identifier: MIT
import Foundation

/// Storage fixtures contain no usable keys and never request authentication.
func checkFIDO2Storage() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("ashacky-fido2-store-" + UUID().uuidString)
    let store = try SecureEnclaveFIDOStore(directory: directory)
    defer { try? FileManager.default.removeItem(at: directory) }
    let state = directory.appendingPathComponent("credentials.json")
    func credential(_ id: Data, user: Data) -> FIDOCredential {
        FIDOCredential(id: id, rp: "example.invalid", user: user, name: "Fixture", displayName: "Fixture",
            discoverable: true, protection: 2, publicKey: Data([4]) + Data(repeating: 0, count: 64), wrappedKey: Data([1]))
    }
    func rejected(_ work: () throws -> Void) {
        do { try work(); preconditionFailure("Unsafe FIDO2 storage accepted") }
        catch { }
    }
    let empty = try store.credentials()
    precondition(empty.isEmpty)
    let reopenedEmpty = try SecureEnclaveFIDOStore(directory: directory).credentials()
    precondition(reopenedEmpty.isEmpty)
    let first = credential(Data(repeating: 0, count: 32), user: Data([0]))
    try store.save(first)
    let saved = try store.credentials()
    precondition(saved.count == 1)
    let reopened = try SecureEnclaveFIDOStore(directory: directory).credentials()
    precondition(reopened.count == 1 && reopened[0].id == first.id)
    let link = directory.appendingPathComponent("directory-link")
    try FileManager.default.createSymbolicLink(at: link, withDestinationURL: directory)
    rejected { _ = try SecureEnclaveFIDOStore(directory: link) }
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: directory.path)
    rejected { _ = try SecureEnclaveFIDOStore(directory: directory) }
    try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
    let permissions = try FileManager.default.attributesOfItem(atPath: state.path)[.posixPermissions] as! NSNumber
    precondition(permissions.intValue == 0o600)
    try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: state.path)
    rejected { _ = try store.credentials() }
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: state.path)
    let backup = directory.appendingPathComponent("fixture-backup")
    try FileManager.default.moveItem(at: state, to: backup)
    try FileManager.default.createSymbolicLink(at: state, withDestinationURL: backup)
    rejected { _ = try store.credentials() }
    try FileManager.default.removeItem(at: state)
    try FileManager.default.moveItem(at: backup, to: state)
    // A full store must still permit replacing one resident RP/user pair.
    let full = (0..<256).map { credential(Data(repeating: UInt8($0), count: 32), user: Data([UInt8($0)])) }
    try JSONEncoder().encode(full).write(to: state)
    let replacement = credential(Data([1, 2]) + Data(repeating: 0, count: 30), user: Data([0]))
    try store.save(replacement)
    let records = try store.credentials()
    precondition(records.count == 256 && records.contains { $0.id == replacement.id })
    precondition(!records.contains { $0.id == first.id })
    rejected { try store.save(credential(Data(repeating: 2, count: 32), user: Data([0, 1]))) }
    print("FIDO2 private storage, persistence, symlink rejection and full-store replacement passed")
}
