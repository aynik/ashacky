import Foundation
import Darwin

enum IPCError: Error { case message(String) }

func socketAddress(_ path: String) throws -> sockaddr_un {
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
    let bytes = Array(path.utf8) + [0]
    guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else { throw IPCError.message("Socket path too long") }
    withUnsafeMutableBytes(of: &address.sun_path) { buffer in buffer.copyBytes(from: bytes) }
    return address
}

func withAddress<T>(_ path: String, _ body: (UnsafePointer<sockaddr>, socklen_t) -> T) throws -> T {
    var address = try socketAddress(path)
    return withUnsafePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { body($0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
    }
}

func prepareSocket(_ fd: Int32, seconds: Int = 10) {
    var yes: Int32 = 1
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout.size(ofValue: yes)))
    var timeout = timeval(tv_sec: seconds, tv_usec: 0)
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout)))
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout)))
}

func receiveJSON(_ fd: Int32) throws -> [String: Any] {
    var bytes = Data()
    var byte: UInt8 = 0
    while bytes.count < 16384 {
        let count = Darwin.read(fd, &byte, 1)
        guard count == 1 else { throw IPCError.message("Connection closed or timed out") }
        if byte == 10 {
            guard let value = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else { throw IPCError.message("Expected JSON object") }
            return value
        }
        bytes.append(byte)
    }
    throw IPCError.message("Request too large")
}

func sendJSON(_ fd: Int32, _ value: [String: Any]) throws {
    var data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    data.append(10)
    try data.withUnsafeBytes { buffer in
        var offset = 0
        while offset < buffer.count {
            let written = Darwin.write(fd, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
            guard written > 0 else { throw IPCError.message("Socket write failed") }
            offset += written
        }
    }
}

func requestSocket(_ path: String, _ value: [String: Any]) throws -> [String: Any] {
    let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { throw IPCError.message("Cannot create socket") }
    defer { Darwin.close(fd) }
    prepareSocket(fd, seconds: 90)
    let result = try withAddress(path) { Darwin.connect(fd, $0, $1) }
    guard result == 0 else { throw IPCError.message("Service unavailable: \(String(cString: strerror(errno)))") }
    try sendJSON(fd, value)
    return try receiveJSON(fd)
}

final class UnixServer {
    private var fd: Int32 = -1
    private let path: String
    init(path: String, mode: mode_t = 0o600, handler: @escaping (Int32, [String: Any]) -> [String: Any]) throws {
        self.path = path
        // Caller must hold its process lock before replacing an old socket.
        unlink(path)
        fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw IPCError.message("Cannot create listener") }
        let result = try withAddress(path) { Darwin.bind(fd, $0, $1) }
        guard result == 0, chmod(path, mode) == 0, Darwin.listen(fd, 8) == 0 else { throw IPCError.message("Cannot bind \(path)") }
        let listener = fd
        DispatchQueue.global(qos: .utility).async {
            while true {
                let client = Darwin.accept(listener, nil, nil)
                if client < 0 { if errno == EINTR { continue }; break }
                prepareSocket(client, seconds: 10)
                // Bounded serial handling avoids unbounded request threads.
                do { try sendJSON(client, handler(client, try receiveJSON(client))) }
                catch { try? sendJSON(client, ["ok": false, "error": String(describing: error)]) }
                Darwin.close(client)
            }
        }
    }
    deinit { if fd >= 0 { Darwin.close(fd) }; unlink(path) }
}

final class RawUnixServer {
    private var fd: Int32 = -1
    private let path: String
    init(path: String, accept: @escaping (Int32) -> Void) throws {
        self.path = path
        unlink(path)
        fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        let result = try withAddress(path) { Darwin.bind(fd, $0, $1) }
        guard fd >= 0, result == 0, chmod(path, 0o600) == 0, Darwin.listen(fd, 4) == 0 else { throw IPCError.message("Cannot bind SSH proxy") }
        let descriptor = fd
        DispatchQueue.global(qos: .utility).async {
            while true {
                let client = Darwin.accept(descriptor, nil, nil)
                if client < 0 { if errno == EINTR { continue }; break }
                prepareSocket(client, seconds: 300)
                accept(client)
            }
        }
    }
    deinit { if fd >= 0 { Darwin.close(fd) }; unlink(path) }
}

func copyStream(from source: Int32, to destination: Int32) {
    var bytes = [UInt8](repeating: 0, count: 65536)
    while true {
        let count = Darwin.read(source, &bytes, bytes.count)
        if count <= 0 { break }
        var offset = 0
        while offset < count {
            let written = bytes.withUnsafeBytes { Darwin.write(destination, $0.baseAddress!.advanced(by: offset), count - offset) }
            if written <= 0 { Darwin.shutdown(source, SHUT_RD); Darwin.shutdown(destination, SHUT_WR); return }
            offset += written
        }
    }
    Darwin.shutdown(destination, SHUT_WR)
}

/// Retain for the entire listener lifetime; private directory validated by caller.
final class WorkbenchProcessLock {
    private let fd: Int32
    init(path: String) throws {
        fd = Darwin.open(path, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw IPCError.message("Cannot open service lock") }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            Darwin.close(fd)
            throw IPCError.message("Service already running")
        }
    }
    deinit { Darwin.close(fd) }
}
