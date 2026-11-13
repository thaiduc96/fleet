import Foundation

/// Start Fleet.app at login with a plain LaunchAgent (SMAppService needs a
/// Developer ID signature; this works for the ad-hoc signed app).
///
/// KeepAlive only on crash: quitting from the menu (exit 0) keeps it quit until
/// the next login. AbandonProcessGroup lets the updater's relaunch helper survive
/// the old app exiting.
enum LoginItem {
    static var plist: URL {
        Env.home.appendingPathComponent("Library/LaunchAgents/\(Env.agentLabel).plist")
    }

    static var legacyPlist: URL {
        Env.home.appendingPathComponent("Library/LaunchAgents/\(Env.legacyAgentLabel).plist")
    }

    private static var domain: String { "gui/\(getuid())" }

    static func contents(executable: String) -> NSDictionary {
        [
            "Label": Env.agentLabel,
            "ProgramArguments": [executable],
            "RunAtLoad": true,
            "KeepAlive": ["SuccessfulExit": false],
            "AbandonProcessGroup": true,
            "ProcessType": "Interactive",
            "LimitLoadToSessionType": "Aqua",
        ]
    }

    /// Write/refresh the agent pointing at the running app. Blocking; call off the main thread.
    static func install() {
        guard Env.managesSystem, let exe = Bundle.main.executablePath else { return }
        let desired = contents(executable: exe)
        if let current = NSDictionary(contentsOf: plist), current.isEqual(desired) {
            return
        }
        try? FileManager.default.createDirectory(at: plist.deletingLastPathComponent(), withIntermediateDirectories: true)
        desired.write(to: plist, atomically: true)
        // Never boot out a loaded job here: we may be that job, and bootout would kill us.
        // A changed plist of a loaded job takes effect at the next login.
        let loaded = Shell.run("/bin/launchctl", ["print", "\(domain)/\(Env.agentLabel)"], timeout: 10).ok
        if !loaded {
            // launchd may start a second copy right away; the single-instance check makes it exit.
            Shell.run("/bin/launchctl", ["bootstrap", domain, plist.path], timeout: 10)
        }
    }

    static func uninstall() {
        Shell.run("/bin/launchctl", ["bootout", "\(domain)/\(Env.agentLabel)"], timeout: 10)
        try? FileManager.default.removeItem(at: plist)
    }

    // MARK: the pre-app install (its own LaunchAgent ran fleetd)

    static var hasLegacyAgent: Bool { FileManager.default.fileExists(atPath: legacyPlist.path) }

    /// Stop the old agent but keep its plist, so it can be restored if ours fails.
    static func stopLegacyAgent() {
        Shell.run("/bin/launchctl", ["bootout", "\(domain)/\(Env.legacyAgentLabel)"], timeout: 15)
    }

    /// Ours works: the old agent is no longer needed (config, token and state stay).
    static func forgetLegacyAgent() {
        try? FileManager.default.removeItem(at: legacyPlist)
    }

    /// Ours failed: bring the old one back so the Mac keeps a working fleetd.
    static func restoreLegacyAgent() {
        Shell.run("/bin/launchctl", ["bootstrap", domain, legacyPlist.path], timeout: 15)
    }
}
