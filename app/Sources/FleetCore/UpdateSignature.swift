import CryptoKit
import Foundation

/// Ed25519 signatures over release zips. The private key lives outside the repo
/// (a GitHub Actions secret, plus a local backup); the app embeds the public key
/// and refuses an update whose signature does not verify, so publishing a
/// release on GitHub is not enough to push code to every Mac.
public enum UpdateSignature {
    public enum KeyError: Error, Equatable { case badKey, badSignature }

    /// New key pair as base64 (raw 32-byte representations).
    public static func generate() -> (privateKey: String, publicKey: String) {
        let key = Curve25519.Signing.PrivateKey()
        return (key.rawRepresentation.base64EncodedString(), key.publicKey.rawRepresentation.base64EncodedString())
    }

    public static func sign(_ data: Data, privateKeyBase64: String) throws -> String {
        guard let raw = Data(base64Encoded: privateKeyBase64.trimmingCharacters(in: .whitespacesAndNewlines)),
              let key = try? Curve25519.Signing.PrivateKey(rawRepresentation: raw) else { throw KeyError.badKey }
        return try key.signature(for: data).base64EncodedString()
    }

    public static func publicKey(ofPrivate privateKeyBase64: String) throws -> String {
        guard let raw = Data(base64Encoded: privateKeyBase64.trimmingCharacters(in: .whitespacesAndNewlines)),
              let key = try? Curve25519.Signing.PrivateKey(rawRepresentation: raw) else { throw KeyError.badKey }
        return key.publicKey.rawRepresentation.base64EncodedString()
    }

    public static func verify(_ data: Data, signatureBase64: String, publicKeyBase64: String) -> Bool {
        guard let rawKey = Data(base64Encoded: publicKeyBase64),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: rawKey),
              let sig = Data(base64Encoded: signatureBase64.trimmingCharacters(in: .whitespacesAndNewlines))
        else { return false }
        return key.isValidSignature(sig, for: data)
    }
}
