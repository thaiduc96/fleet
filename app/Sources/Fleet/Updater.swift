import AppKit
import CryptoKit
import FleetCore

/// Self-update from GitHub Releases.
///
/// A release is only installed if Fleet-x.y.z.zip carries a valid Ed25519
/// signature (Fleet-x.y.z.zip.sig) from the release key whose public half is
/// embedded in the app, and matches checksums.txt. The zip is downloaded with
/// URLSession (no quarantine flag), unpacked with ditto, its code signature,
/// version and bundle id checked, then swapped in by renaming folders (the old
/// app is kept as Fleet.app.old until the next start). All heavy work runs off
/// the main thread.
final class Updater: @unchecked Sendable {
    enum Outcome: Equatable { case upToDate, notSigned(String), installing(String), failed(String) }

    private let lock = NSLock()
    private var _busy = false
    private var _lastCheck: Date?
    var busy: Bool { lock.lock(); defer { lock.unlock() }; return _busy }
    var lastCheck: Date? {
        get { lock.lock(); defer { lock.unlock() }; return _lastCheck }
        set { lock.lock(); _lastCheck = newValue; lock.unlock() }
    }

    func latest() async throws -> Release? {
        var req = URLRequest(url: URL(string: "https://api.github.com/repos/\(Env.repo)/releases/latest")!)
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 20
        let (data, resp) = try await URLSession.shared.data(for: req)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if status == 404 { return nil }  // no release yet
        guard status == 200 else { throw Setup.Failure(message: "GitHub trả về \(status)") }
        do { return try Release.parse(data) } catch Release.ParseError.missingAsset(let name) where name.hasSuffix(".sig") {
            throw UnsignedRelease()
        }
    }

    struct UnsignedRelease: Error {}

    /// Mark busy (false if an update is already running). Synchronous: locks never span an await.
    private func claim() -> Bool {
        lock.lock(); defer { lock.unlock() }
        if _busy { return false }
        _busy = true
        _lastCheck = Date()
        return true
    }

    private func release() {
        lock.lock(); _busy = false; lock.unlock()
    }

    /// Check, and install if newer. Returns what happened (for the menu / dialogs).
    func run() async -> Outcome {
        guard claim() else { return .failed("Đang cập nhật") }
        defer { release() }
        do {
            guard let current = Version(Env.appVersion) else { return .failed("Phiên bản app không hợp lệ") }
            let release: Release?
            do { release = try await latest() } catch is UnsignedRelease {
                return .notSigned("Bản mới trên GitHub chưa được ký — bỏ qua")
            }
            guard let release, current < release.version else { return .upToDate }
            guard Env.managesSystem else { return .failed("Chỉ tự cập nhật được khi chạy từ Fleet.app đã cài") }
            let folder = Bundle.main.bundleURL.deletingLastPathComponent()
            guard FileManager.default.isWritableFile(atPath: folder.path) else {
                return .failed("Không ghi được vào \(folder.path); cài lại bằng install.sh")
            }
            try await install(release)
            return .installing(release.version.description)
        } catch {
            return .failed(error.localizedDescription)
        }
    }

    private func install(_ release: Release) async throws {
        let fm = FileManager.default
        let work = fm.temporaryDirectory.appendingPathComponent("fleet-update-\(UUID().uuidString)")
        try fm.createDirectory(at: work, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: work) }

        let zipName = "Fleet-\(release.version).zip"
        let (sumsData, _) = try await URLSession.shared.data(from: release.checksumsURL)
        guard let expected = Release.checksum(for: zipName, in: String(decoding: sumsData, as: UTF8.self)) else {
            throw Setup.Failure(message: "checksums.txt không có \(zipName)")
        }
        let (sigData, _) = try await URLSession.shared.data(from: release.signatureURL)
        let (tmpZip, _) = try await URLSession.shared.download(from: release.zipURL)
        let zip = work.appendingPathComponent(zipName)
        try fm.moveItem(at: tmpZip, to: zip)

        let target = Bundle.main.bundleURL
        try await Shell.background { () -> Result<Void, Setup.Failure> in
            Result { try Self.verifyAndSwap(zip: zip, expectedSHA: expected, signature: sigData,
                                            version: release.version.description, work: work, target: target) }
                .mapError { ($0 as? Setup.Failure) ?? Setup.Failure(message: $0.localizedDescription) }
        }.get()
        await MainActor.run { Self.relaunch(target) }
    }

    /// Blocking checks + swap; runs on a background thread.
    private static func verifyAndSwap(zip: URL, expectedSHA: String, signature: Data, version: String,
                                      work: URL, target: URL) throws {
        let fm = FileManager.default
        guard let handle = try? FileHandle(forReadingFrom: zip) else { throw Setup.Failure(message: "Không đọc được zip") }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty { hasher.update(data: chunk) }
        try handle.close()
        let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()
        guard digest == expectedSHA else { throw Setup.Failure(message: "Sai checksum, bỏ qua bản tải về") }
        let zipData = try Data(contentsOf: zip, options: .mappedIfSafe)
        guard UpdateSignature.verify(zipData, signatureBase64: String(decoding: signature, as: UTF8.self),
                                     publicKeyBase64: Env.updatePublicKey) else {
            throw Setup.Failure(message: "Chữ ký bản cập nhật không hợp lệ — bỏ qua")
        }

        let unpacked = work.appendingPathComponent("unpacked")
        let unzip = Shell.run("/usr/bin/ditto", ["-x", "-k", zip.path, unpacked.path], timeout: 120)
        guard unzip.ok else { throw Setup.Failure(message: "Giải nén lỗi: \(unzip.stderr)") }
        let newApp = unpacked.appendingPathComponent("Fleet.app")
        let verify = Shell.run("/usr/bin/codesign", ["--verify", "--deep", "--strict", newApp.path], timeout: 180)
        guard verify.ok else { throw Setup.Failure(message: "Chữ ký app mới không hợp lệ: \(verify.stderr)") }
        let info = NSDictionary(contentsOf: newApp.appendingPathComponent("Contents/Info.plist"))
        guard (info?["CFBundleShortVersionString"] as? String) == version,
              (info?["CFBundleIdentifier"] as? String) == Env.bundleID else {
            throw Setup.Failure(message: "App tải về không đúng phiên bản \(version)")
        }

        let old = target.deletingLastPathComponent().appendingPathComponent("Fleet.app.old")
        try? fm.removeItem(at: old)
        try fm.moveItem(at: target, to: old)
        do {
            try fm.moveItem(at: newApp, to: target)
        } catch {
            try? fm.moveItem(at: old, to: target)  // put the running version back
            throw Setup.Failure(message: "Không thay được app: \(error.localizedDescription)")
        }
    }

    /// Start the new copy once this process has fully exited. The helper runs in its own
    /// session (and the LaunchAgent sets AbandonProcessGroup), so launchd does not kill it.
    @MainActor
    private static func relaunch(_ app: URL) {
        let pid = String(ProcessInfo.processInfo.processIdentifier)
        let script = "while kill -0 \"$1\" 2>/dev/null; do sleep 0.2; done; /usr/bin/open \"$0\""
        _ = Shell.spawnDetached("/bin/sh", ["-c", script, app.path, pid])
        NSApp.terminate(nil)
    }

    /// Remove the previous version kept by the last update.
    static func cleanUpOldCopy() {
        let old = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("Fleet.app.old")
        try? FileManager.default.removeItem(at: old)
    }
}
