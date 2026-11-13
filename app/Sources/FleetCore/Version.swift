import Foundation

/// Semantic version "MAJOR.MINOR.PATCH[-pre]" ("v" prefix allowed, as in git tags).
public struct Version: Comparable, CustomStringConvertible, Sendable {
    public let major: Int
    public let minor: Int
    public let patch: Int
    public let prerelease: String?

    public init?(_ text: String) {
        var s = text.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("v") { s.removeFirst() }
        let parts = s.split(separator: "-", maxSplits: 1, omittingEmptySubsequences: false)
        guard let core = parts.first else { return nil }
        let nums = core.split(separator: ".")
        guard nums.count == 3, let a = Int(nums[0]), let b = Int(nums[1]), let c = Int(nums[2]),
              a >= 0, b >= 0, c >= 0 else { return nil }
        major = a; minor = b; patch = c
        prerelease = parts.count == 2 ? String(parts[1]) : nil
    }

    public var description: String {
        "\(major).\(minor).\(patch)" + (prerelease.map { "-\($0)" } ?? "")
    }

    public static func < (l: Version, r: Version) -> Bool {
        if (l.major, l.minor, l.patch) != (r.major, r.minor, r.patch) {
            return (l.major, l.minor, l.patch) < (r.major, r.minor, r.patch)
        }
        // 1.0.0-dev < 1.0.0 ; two pre-releases compare as text
        switch (l.prerelease, r.prerelease) {
        case (nil, nil): return false
        case (.some, nil): return true
        case (nil, .some): return false
        case let (.some(a), .some(b)): return a < b
        }
    }
}
