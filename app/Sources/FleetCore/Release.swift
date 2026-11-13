import Foundation

/// The parts of a GitHub "latest release" response the updater needs.
public struct Release: Sendable, Equatable {
    public let version: Version
    public let zipURL: URL
    public let checksumsURL: URL
    public let signatureURL: URL

    public static func == (l: Release, r: Release) -> Bool {
        l.version.description == r.version.description && l.zipURL == r.zipURL
            && l.checksumsURL == r.checksumsURL && l.signatureURL == r.signatureURL
    }

    public enum ParseError: Error, Equatable {
        case badJSON
        case badTag(String)
        case missingAsset(String)
    }

    private struct API: Decodable {
        struct Asset: Decodable {
            let name: String
            let browser_download_url: String
        }
        let tag_name: String
        let draft: Bool?
        let prerelease: Bool?
        let assets: [Asset]
    }

    /// Parses `GET /repos/<owner>/<repo>/releases/latest`.
    public static func parse(_ data: Data) throws -> Release {
        guard let api = try? JSONDecoder().decode(API.self, from: data) else { throw ParseError.badJSON }
        guard let version = Version(api.tag_name) else { throw ParseError.badTag(api.tag_name) }
        let zipName = "Fleet-\(version).zip"
        func asset(_ name: String) throws -> URL {
            guard let a = api.assets.first(where: { $0.name == name }), let url = URL(string: a.browser_download_url)
            else { throw ParseError.missingAsset(name) }
            return url
        }
        return Release(version: version, zipURL: try asset(zipName), checksumsURL: try asset("checksums.txt"),
                       signatureURL: try asset(zipName + ".sig"))
    }

    /// `checksums.txt` as written by `shasum -a 256`: "<hex>  <file>" per line.
    public static func checksum(for file: String, in text: String) -> String? {
        for line in text.split(whereSeparator: \.isNewline) {
            let parts = line.split(separator: " ", omittingEmptySubsequences: true)
            guard parts.count == 2 else { continue }
            let name = parts[1].hasPrefix("*") ? String(parts[1].dropFirst()) : String(parts[1])
            let hex = String(parts[0]).lowercased()
            if name == file, hex.count == 64, hex.allSatisfy(\.isHexDigit) { return hex }
        }
        return nil
    }
}
