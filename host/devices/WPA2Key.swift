import Foundation
import CommonCrypto

enum WPA2Key {
    enum Failure: Error { case invalidPassphrase, invalidSSID, derivation }
    // WPA2 PSK: PBKDF2-HMAC-SHA1(passphrase, raw SSID, 4096), 256 bits.
    // Test harness only: never persist or log the returned key.
    static func derive(passphrase: String, ssid: Data) throws -> String {
        let bytes = Array(passphrase.utf8)
        guard (8...63).contains(bytes.count), bytes.allSatisfy({ $0 >= 32 && $0 <= 126 }) else {
            throw Failure.invalidPassphrase
        }
        guard (1...32).contains(ssid.count) else { throw Failure.invalidSSID }
        var key = [UInt8](repeating: 0, count: 32)
        let result = bytes.withUnsafeBytes { password in
            ssid.withUnsafeBytes { salt in
                CCKeyDerivationPBKDF(CCPBKDFAlgorithm(kCCPBKDF2),
                    password.baseAddress!.assumingMemoryBound(to: Int8.self), bytes.count,
                    salt.baseAddress!.assumingMemoryBound(to: UInt8.self), ssid.count,
                    CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA1), 4096, &key, key.count)
            }
        }
        guard result == kCCSuccess else { throw Failure.derivation }
        return key.map { String(format: "%02x", $0) }.joined()
    }
}
