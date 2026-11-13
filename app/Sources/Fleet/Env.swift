import Foundation

/// Where things live, shared with fleetd (same ~/.fleet layout as the CLI install).
enum Env {
    static let repo = "thaiduc96/fleet"
    static let bundleID = "com.fleet.app"
    static let agentLabel = "com.fleet.app"
    static let legacyAgentLabel = "com.fleet.fleetd"
    /// Ed25519 public key that release zips must be signed with (private key: the
    /// FLEET_SIGNING_KEY Actions secret; backup in ~/.fleet/release-signing-key on the dev Mac).
    static let updatePublicKey = "dsxT85ZoXy8KZM1+Vf2XLSJrtiaxtbER1rtTzYdC/5k="

    static var home: URL { FileManager.default.homeDirectoryForCurrentUser }

    static var fleetHome: URL {
        if let custom = ProcessInfo.processInfo.environment["FLEET_HOME"], !custom.isEmpty {
            return URL(fileURLWithPath: custom)
        }
        return home.appendingPathComponent(".fleet")
    }

    static var configFile: URL { fleetHome.appendingPathComponent("config.toml") }
    static var logFile: URL { fleetHome.appendingPathComponent("fleetd.log") }
    static var updateRequest: URL { fleetHome.appendingPathComponent("update-request") }
    /// Present while onboarding has not finished; a failed setup shows onboarding again.
    static var setupPending: URL { fleetHome.appendingPathComponent("setup-pending") }

    /// `machine = "…"` from an existing config.toml (e.g. the old CLI install).
    static var configuredMachine: String? {
        guard let text = try? String(contentsOf: configFile, encoding: .utf8) else { return nil }
        for line in text.split(whereSeparator: \.isNewline) {
            let parts = line.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            if parts.count == 2, parts[0] == "machine" {
                return parts[1].trimmingCharacters(in: CharacterSet(charactersIn: "\""))
            }
        }
        return nil
    }

    /// `port = N` from config.toml (default 7777).
    static var port: Int {
        guard let text = try? String(contentsOf: configFile, encoding: .utf8) else { return 7777 }
        for line in text.split(whereSeparator: \.isNewline) {
            let parts = line.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            if parts.count == 2, parts[0] == "port", let p = Int(parts[1]) { return p }
        }
        return 7777
    }

    static var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }

    static var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0-dev"
    }

    /// True when running from a real Fleet.app bundle (not `swift run`).
    static var isBundled: Bool { Bundle.main.bundleIdentifier == bundleID }

    /// May the app change the system (LaunchAgents, removing the old install)?
    /// FLEET_DEV=1 runs a real bundle side by side with an installed Fleet for testing.
    static var managesSystem: Bool {
        isBundled && ProcessInfo.processInfo.environment["FLEET_DEV"] != "1"
    }

    /// Python + fleetd shipped inside the app.
    static var bundledPython: URL? {
        guard let res = Bundle.main.resourceURL else { return nil }
        let py = res.appendingPathComponent("backend/python/bin/python3")
        return FileManager.default.isExecutableFile(atPath: py.path) ? py : nil
    }

    /// Machine id suggestion from the computer name: lowercase letters, digits, dashes.
    static var suggestedMachineID: String {
        let raw = Host.current().localizedName ?? ProcessInfo.processInfo.hostName
        let cleaned = raw.lowercased().map { $0.isLetter || $0.isNumber ? $0 : "-" }
        let collapsed = String(cleaned).split(separator: "-").joined(separator: "-")
        return String(collapsed.prefix(40)).isEmpty ? "mac" : String(collapsed.prefix(40))
    }
}
