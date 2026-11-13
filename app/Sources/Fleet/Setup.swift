import Foundation
import FleetCore

/// First-run setup and the "who am I on the tailnet" bookkeeping.
enum Setup {
    /// Configured = config.toml + a token, and no unfinished onboarding.
    /// (True for the old CLI install too.) Uses the cached token: cheap on the main thread.
    static var isConfigured: Bool {
        let fm = FileManager.default
        return fm.fileExists(atPath: Env.configFile.path) && !fm.fileExists(atPath: Env.setupPending.path)
            && TokenStore.read() != nil
    }

    static func writeConfig(machine: String, label: String, port: Int) throws {
        try FileManager.default.createDirectory(at: Env.fleetHome, withIntermediateDirectories: true)
        let esc = { (s: String) in s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") }
        let text = """
        machine = "\(esc(machine))"
        label = "\(esc(label))"
        port = \(port)

        # Managed by Fleet.app. Other machines are added with invite codes (Fleet → Mời máy mới…).

        """
        try text.write(to: Env.configFile, atomically: true, encoding: .utf8)
    }

    enum Choice { case create; case join(InviteCode) }

    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    /// Everything after the user picked a name and create/join. Nothing is committed
    /// as "configured" until every step succeeded (the setup-pending marker).
    /// `progress` gets short Vietnamese status lines for the UI.
    static func run(choice: Choice, machine: String, label: String, keepAwakeOnAC: Bool,
                    backend: Backend, progress: @escaping @Sendable (String) -> Void) async throws {
        let ts = await Shell.background { Tailscale.status() }
        guard ts.running, let myURL = ts.url else {
            throw Failure(message: "Tailscale chưa chạy. Mở app Tailscale, đăng nhập, rồi thử lại.")
        }
        try FileManager.default.createDirectory(at: Env.fleetHome, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: Env.setupPending.path, contents: Data())

        // 1. Token: keep/create ours, or take the invite's after the inviter accepted it.
        let token: String
        switch choice {
        case .create:
            token = await Shell.background { TokenStore.load() } ?? TokenStore.generate()
        case .join(let invite):
            progress("Kiểm tra mã mời…")
            guard let inviterURL = URL(string: invite.url) else { throw Failure(message: "Mã mời hỏng") }
            let hello: FleetClient.Hello
            do { hello = try await FleetClient(base: inviterURL).hello() } catch {
                throw Failure(message: "Không kết nối được \(invite.url): \(error.localizedDescription). "
                                     + "Máy đó phải đang mở Fleet và cùng tailnet.")
            }
            guard hello.fleet else { throw Failure(message: "\(invite.url) không phải Fleet") }
            if hello.machine == machine {
                throw Failure(message: "Tên '\(machine)' trùng với máy mời; đặt tên khác.")
            }
            do { _ = try await FleetClient(base: inviterURL, token: invite.token).local() } catch {
                throw Failure(message: "Máy mời không nhận mã này (\(error.localizedDescription)). "
                                     + "Tạo mã mời mới trên máy đó rồi thử lại.")
            }
            token = invite.token
        }
        let stored = await Shell.background { TokenStore.write(token) }
        guard stored else { throw Failure(message: "Không lưu được token vào Keychain") }

        // 2. Config + backend (restarted so it picks up the name and token).
        progress("Ghi cấu hình…")
        try writeConfig(machine: machine, label: label, port: Env.port)
        progress("Khởi động fleetd…")
        backend.restart()
        let me = FleetClient(token: token)
        var ready = false
        for _ in 0..<40 {
            if let h = try? await me.hello(), h.machine == machine { ready = true; break }
            try await Task.sleep(nanoseconds: 500_000_000)
        }
        guard ready else { throw Failure(message: "fleetd không khởi động được — xem ~/.fleet/fleetd.log") }

        // 3. Reachable from the tailnet.
        progress("Bật tailscale serve…")
        let port = Env.port
        let serveError: String? = await Shell.background {
            Tailscale.serving(port: port) ? nil : Tailscale.serve(port: port)
        }
        if let err = serveError {
            throw Failure(message: "Chưa mở được Fleet cho tailnet: \(err)\n"
                                 + "Nếu Tailscale báo Serve chưa bật, mở link đó, bật, rồi thử lại.")
        }
        try await me.settings(label: label, url: myURL)

        // 4. Join: introduce ourselves, then learn everyone the inviter knows.
        if case .join(let invite) = choice, let inviterURL = URL(string: invite.url) {
            progress("Ghép với máy mời…")
            let roster = try await FleetClient(base: inviterURL, token: token)
                .join(machine: machine, label: label, url: myURL)
            var entries: [[String: Any]] = (roster["peers"] as? [[String: Any]]) ?? []
            entries.insert(["machine": roster["machine"] ?? "", "label": roster["label"] ?? "",
                            "url": roster["url"] as? String ?? invite.url], at: 0)
            for e in entries {
                guard let m = e["machine"] as? String, let u = e["url"] as? String, m != machine else { continue }
                _ = try? await me.join(machine: m, label: (e["label"] as? String) ?? m, url: u)
            }
        }

        if keepAwakeOnAC { try? await me.setAwake("ac") }
        try? FileManager.default.removeItem(at: Env.setupPending)
        progress("Xong")
    }

    /// On every start: keep fleetd's idea of our URL in sync with Tailscale (off the main thread).
    static func syncOwnURL() async {
        let port = Env.port
        let url: String? = await Shell.background {
            let ts = Tailscale.status()
            return ts.running && Tailscale.serving(port: port) ? ts.url : nil
        }
        guard let url, let token = TokenStore.read() else { return }
        try? await FleetClient(token: token).settings(url: url)
    }
}
