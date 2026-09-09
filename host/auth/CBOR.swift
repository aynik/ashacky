// SPDX-License-Identifier: MIT
import Foundation

/// The definite-length CBOR subset used by CTAP2. Bounded before allocation.
/// Keys are integers or text; floats, tags, duplicate keys and trailing data fail.
enum FIDOError: Error {
    case status(UInt8)
}

enum CBORKey: Hashable {
    case int(Int64), text(String)
    var value: CBOR {
        switch self { case .int(let n): return .int(n); case .text(let s): return .text(s) }
    }
}

indirect enum CBOR: Equatable {
    case int(Int64), bytes(Data), text(String), bool(Bool), array([CBOR]), map([CBORKey: CBOR])

    var integer: Int64? { if case .int(let n) = self { return n }; return nil }
    var data: Data? { if case .bytes(let d) = self { return d }; return nil }
    var string: String? { if case .text(let s) = self { return s }; return nil }
    var boolean: Bool? { if case .bool(let b) = self { return b }; return nil }
    var values: [CBOR]? { if case .array(let a) = self { return a }; return nil }
    var fields: [CBORKey: CBOR]? { if case .map(let m) = self { return m }; return nil }
    subscript(_ key: Int64) -> CBOR? { fields?[.int(key)] }
    subscript(_ key: String) -> CBOR? { fields?[.text(key)] }

    func encoded() -> Data {
        func head(_ major: UInt8, _ n: UInt64) -> Data {
            if n < 24 { return Data([major << 5 | UInt8(n)]) }
            let size = n <= 0xff ? 1 : n <= 0xffff ? 2 : n <= 0xffffffff ? 4 : 8
            let ai: UInt8 = size == 1 ? 24 : size == 2 ? 25 : size == 4 ? 26 : 27
            return Data([major << 5 | ai] + (0..<size).reversed().map { UInt8(truncatingIfNeeded: n >> ($0 * 8)) })
        }
        switch self {
        case .int(let n): return n >= 0 ? head(0, UInt64(n)) : head(1, UInt64(-(n + 1)))
        case .bytes(let d): return head(2, UInt64(d.count)) + d
        case .text(let s): let d = Data(s.utf8); return head(3, UInt64(d.count)) + d
        case .bool(let b): return Data([b ? 0xf5 : 0xf4])
        case .array(let a): return a.reduce(head(4, UInt64(a.count))) { $0 + $1.encoded() }
        case .map(let m):
            let pairs = m.map { ($0.key.value.encoded(), $0.value.encoded()) }.sorted {
                $0.0.count == $1.0.count ? $0.0.lexicographicallyPrecedes($1.0) : $0.0.count < $1.0.count
            }
            return pairs.reduce(head(5, UInt64(pairs.count))) { $0 + $1.0 + $1.1 }
        }
    }

    static func decode(_ data: Data) throws -> CBOR {
        guard data.count <= 7609 else { throw FIDOError.status(0x15) }
        let bytes = Array(data)
        var offset = 0, nodes = 0
        func take(_ count: Int) throws -> Data {
            guard count >= 0, count <= bytes.count - offset else { throw FIDOError.status(0x12) }
            defer { offset += count }; return Data(bytes[offset..<offset + count])
        }
        func read(_ depth: Int) throws -> CBOR {
            nodes += 1
            guard depth < 10, nodes <= 1024, offset < bytes.count else { throw FIDOError.status(0x12) }
            let first = bytes[offset]; offset += 1
            let major = first >> 5, ai = first & 31
            if first == 0xf4 { return .bool(false) }
            if first == 0xf5 { return .bool(true) }
            guard major <= 5, ai <= 27 else { throw FIDOError.status(0x12) }
            let n: UInt64
            if ai < 24 { n = UInt64(ai) }
            else {
                let count = 1 << Int(ai - 24)
                let raw = try take(count)
                n = raw.reduce(UInt64(0)) { ($0 << 8) | UInt64($1) }
                let minimum: UInt64 = count == 1 ? 24 : count == 2 ? 256 : count == 4 ? 65536 : 4294967296
                guard n >= minimum else { throw FIDOError.status(0x12) }
            }
            guard n <= UInt64(Int64.max) else { throw FIDOError.status(0x12) }
            if major == 0 { return .int(Int64(n)) }
            if major == 1 { return .int(-1 - Int64(n)) }
            guard n <= 7609 else { throw FIDOError.status(0x12) }
            switch major {
            case 2: return .bytes(try take(Int(n)))
            case 3:
                guard let text = String(data: try take(Int(n)), encoding: .utf8) else { throw FIDOError.status(0x12) }
                return .text(text)
            case 4: return .array(try (0..<Int(n)).map { _ in try read(depth + 1) })
            case 5:
                var result: [CBORKey: CBOR] = [:]
                for _ in 0..<Int(n) {
                    let key: CBORKey
                    switch try read(depth + 1) {
                    case .int(let i): key = .int(i)
                    case .text(let t): key = .text(t)
                    default: throw FIDOError.status(0x12)
                    }
                    guard result[key] == nil else { throw FIDOError.status(0x12) }
                    result[key] = try read(depth + 1)
                }
                return .map(result)
            default: throw FIDOError.status(0x12)
            }
        }
        let result = try read(0)
        guard offset == bytes.count else { throw FIDOError.status(0x12) }
        return result
    }
}
