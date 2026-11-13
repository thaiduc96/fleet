import Foundation

/// What a new Mac needs to join a fleet: one existing machine's URL and the shared token.
///
/// Text form: `fleet1.<base64url(JSON)>`. It contains the token, so it must only be
/// pasted on your own machines.
public struct InviteCode: Equatable, Sendable {
    public static let prefix = "fleet1."

    public let url: String
    public let token: String

    public init(url: String, token: String) {
        self.url = url
        self.token = token
    }

    public enum DecodeError: Error, Equatable {
        case wrongPrefix
        case notBase64
        case badPayload
        case badURL
        case badToken
    }

    private struct Payload: Codable {
        let url: String
        let token: String
    }

    public func encoded() -> String {
        let data = (try? JSONEncoder().encode(Payload(url: url, token: token))) ?? Data()
        return Self.prefix + Base64URL.encode(data)
    }

    public static func decode(_ text: String) throws -> InviteCode {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasPrefix(prefix) else { throw DecodeError.wrongPrefix }
        guard let data = Base64URL.decode(String(trimmed.dropFirst(prefix.count))) else {
            throw DecodeError.notBase64
        }
        guard let payload = try? JSONDecoder().decode(Payload.self, from: data) else {
            throw DecodeError.badPayload
        }
        guard let url = URL(string: payload.url), let scheme = url.scheme,
              ["http", "https"].contains(scheme), url.host != nil else {
            throw DecodeError.badURL
        }
        guard payload.token.count >= 16, payload.token.allSatisfy({ $0.isLetter || $0.isNumber }) else {
            throw DecodeError.badToken
        }
        var clean = payload.url
        while clean.hasSuffix("/") { clean.removeLast() }
        return InviteCode(url: clean, token: payload.token)
    }
}

public enum Base64URL {
    public static func encode(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    public static func decode(_ text: String) -> Data? {
        var s = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while s.count % 4 != 0 { s += "=" }
        return Data(base64Encoded: s)
    }
}
