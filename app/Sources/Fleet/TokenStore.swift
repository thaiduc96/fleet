import Foundation
import Security

/// The shared fleet token lives in the login Keychain (service "fleet.token",
/// account "fleet") — the same item fleetd and the old CLI install use.
///
/// We go through /usr/bin/security on purpose: the item's ACL already trusts that
/// tool, while an ad-hoc signed app gets a new code identity on every update and
/// would trigger a Keychain prompt each time. The value is cached in memory so the
/// UI never waits on a subprocess; call `invalidate()` after a 401.
enum TokenStore {
    static let service = "fleet.token"
    static let account = "fleet"
    private static let security = "/usr/bin/security"
    private static let lock = NSLock()
    private static var cached: String??   // nil = not loaded yet; .some(nil) = no token

    /// Cached token; the first call (or one after `invalidate`) runs `security`.
    /// Call `load()` off the main thread at startup so this never blocks the UI.
    static func read() -> String? {
        lock.lock()
        if let value = cached { lock.unlock(); return value }
        lock.unlock()
        return load()
    }

    @discardableResult
    static func load() -> String? {
        let r = Shell.run(security, ["find-generic-password", "-s", service, "-a", account, "-w"], timeout: 10)
        let token = r.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        let value: String? = r.ok && !token.isEmpty ? token : nil
        lock.lock(); cached = .some(value); lock.unlock()
        return value
    }

    static func invalidate() {
        lock.lock(); cached = nil; lock.unlock()
    }

    /// Store the token. It is passed on stdin to `security -i`, never in argv
    /// (where `ps` would show it).
    @discardableResult
    static func write(_ token: String) -> Bool {
        guard token.allSatisfy({ $0.isLetter || $0.isNumber }) else { return false }
        let command = "add-generic-password -U -s \(service) -a \(account) -w \(token)\n"
        let ok = Shell.run(security, ["-i"], input: command, timeout: 10).ok
        if ok { lock.lock(); cached = .some(token); lock.unlock() }
        return ok
    }

    /// 48 hex characters from the system CSPRNG (same format as `bin/fleet token init`).
    static func generate() -> String {
        var bytes = [UInt8](repeating: 0, count: 24)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return bytes.map { String(format: "%02x", $0) }.joined()
    }
}
