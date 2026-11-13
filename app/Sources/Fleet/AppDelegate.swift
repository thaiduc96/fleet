import AppKit
import SwiftUI
import FleetCore

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let backend = Backend()
    private let notifier = Notifier()
    private let dashboard = DashboardWindow()
    private let updater = Updater()
    private var statusItem: NSStatusItem!
    private var summary: FleetSummary?
    private var lastError: String?
    private var timer: Timer?
    private var windows: [String: NSWindow] = [:]
    private var polling = false
    private var sigterm: DispatchSourceSignal?
    private var started = false

    // MARK: lifecycle

    func applicationDidFinishLaunching(_ note: Notification) {
        if anotherInstanceRunning() { exit(0) }
        NSApp.setActivationPolicy(.accessory)
        // launchctl / kill send SIGTERM: quit cleanly so fleetd is stopped too.
        signal(SIGTERM, SIG_IGN)
        let src = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        src.setEventHandler { NSApp.terminate(nil) }
        src.resume()
        sigterm = src
        Updater.cleanUpOldCopy()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = NSImage(systemSymbolName: "square.stack.3d.up", accessibilityDescription: "Fleet")
        statusItem.button?.imagePosition = .imageLeading
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu

        notifier.onOpen = { [weak self] key in self?.dashboard.show(focus: key) }
        notifier.setUp()
        backend.onChange = { [weak self] _ in self?.render() }

        Task {
            _ = await Shell.background { TokenStore.load() }  // warm the cache off the main thread
            if Env.managesSystem, LoginItem.hasLegacyAgent {
                await migrateFromLegacyInstall()
            } else {
                backend.start()
            }
            if Env.managesSystem { await Shell.background { LoginItem.install() } }
            if Setup.isConfigured {
                await Setup.syncOwnURL()
            } else {
                showOnboarding()
            }
            started = true
            await tick()
        }
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.tick() }
        }
        // First update check a minute after start, then every 6 hours (in tick).
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { [weak self] in
            Task { await self?.autoUpdate(force: false) }
        }
    }

    /// The pre-app install ran fleetd from its own LaunchAgent. Stop it, start ours,
    /// and only delete the old agent once ours answers; otherwise put the old one back.
    private func migrateFromLegacyInstall() async {
        await Shell.background { LoginItem.stopLegacyAgent() }
        await waitForPortToFree()
        backend.start()
        for _ in 0..<40 {
            if backend.mode == .child, (try? await FleetClient().hello())?.fleet == true {
                await Shell.background { LoginItem.forgetLegacyAgent() }
                return
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        backend.stop()
        await Shell.background { LoginItem.restoreLegacyAgent() }
        lastError = "fleetd của app không chạy được; đã giữ lại bản cài cũ (xem ~/.fleet/fleetd.log)"
    }

    func applicationWillTerminate(_ note: Notification) {
        backend.stop()
    }

    private func anotherInstanceRunning() -> Bool {
        guard let id = Bundle.main.bundleIdentifier else { return false }
        let me = ProcessInfo.processInfo.processIdentifier
        return NSRunningApplication.runningApplications(withBundleIdentifier: id).contains { $0.processIdentifier != me }
    }

    /// Wait (≤ 10 s) until nothing answers on our port at all. An old fleetd without
    /// /api/hello still answers (404), so a failing hello does not mean "free".
    private func waitForPortToFree() async {
        for _ in 0..<33 {
            if await Shell.background({ Backend.portOwner() }) == .nobody { return }
            try? await Task.sleep(nanoseconds: 300_000_000)
        }
    }

    // MARK: polling

    private func tick() async {
        guard started, !polling else { return }
        polling = true
        defer { polling = false }
        if FileManager.default.fileExists(atPath: Env.updateRequest.path) {
            try? FileManager.default.removeItem(at: Env.updateRequest)
            await autoUpdate(force: true)
        } else if let last = updater.lastCheck, Date().timeIntervalSince(last) > 6 * 3600 {
            await autoUpdate(force: false)
        }
        guard Setup.isConfigured, let token = TokenStore.read() else { render(); return }
        do {
            let s = try await FleetClient(token: token).fleet()
            summary = s
            lastError = nil
            notifier.observe(s)
        } catch {
            lastError = error.localizedDescription
            if (error as? FleetClient.APIError)?.status == 401 {
                _ = await Shell.background { TokenStore.invalidate(); return TokenStore.load() }  // token changed
            }
            if backend.mode == .external { backend.start() }  // an external fleetd went away: run ours
        }
        render()
    }

    private func autoUpdate(force: Bool) async {
        let enabled = UserDefaults.standard.object(forKey: "autoUpdate") as? Bool ?? true
        guard force || enabled else { updater.lastCheck = Date(); return }
        switch await updater.run() {
        case .failed(let msg) where force, .notSigned(let msg) where force: lastError = "Cập nhật: \(msg)"
        default: break
        }
    }

    // MARK: menu

    private func render() {
        let count = summary?.needsInput.count ?? 0
        statusItem.button?.title = count > 0 ? " \(count)" : ""
        statusItem.button?.contentTintColor = count > 0 ? .systemOrange : nil
        statusItem.button?.toolTip = count > 0 ? "\(count) session cần bạn trả lời" : "Fleet"
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        func add(_ title: String, _ action: Selector?, key: String = "", enabled: Bool = true) -> NSMenuItem {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.target = self
            item.isEnabled = enabled && action != nil
            menu.addItem(item)
            return item
        }
        if !Setup.isConfigured {
            _ = add("Thiết lập Fleet…", #selector(openOnboarding))
        } else if let s = summary {
            let online = s.machines.filter(\.online)
            _ = add("Fleet — \(online.count)/\(s.machines.count) máy online", nil, enabled: false)
            for (title, items, icon) in [("Cần bạn trả lời", s.needsInput, "exclamationmark.bubble"),
                                         ("Đang chạy", s.working, "play.circle")] where !items.isEmpty {
                menu.addItem(.separator())
                _ = add(title, nil, enabled: false)
                for item in items.prefix(8) {
                    let mi = add("\(item.session.name ?? item.session.id) · \(item.machineLabel)", #selector(openSession(_:)))
                    mi.representedObject = item.id
                    mi.image = NSImage(systemSymbolName: icon, accessibilityDescription: nil)
                }
            }
        } else {
            _ = add(lastError.map { "Chưa kết nối: \($0)" } ?? "Đang khởi động…", nil, enabled: false)
        }
        if case .unavailable(let why) = backend.mode { _ = add("⚠︎ \(why)", nil, enabled: false) }
        menu.addItem(.separator())
        _ = add("Mở dashboard", #selector(openDashboard), key: "d")
        _ = add("Mời máy mới…", #selector(openInvite), enabled: Setup.isConfigured)
        _ = add("Cài đặt…", #selector(openSettings), key: ",", enabled: Setup.isConfigured)
        _ = add(updater.busy ? "Đang cập nhật…" : "Kiểm tra cập nhật", #selector(checkUpdate), enabled: !updater.busy)
        menu.addItem(.separator())
        _ = add("Fleet \(Env.appVersion)", nil, enabled: false)
        _ = add("Thoát Fleet", #selector(quit), key: "q")
    }

    // MARK: actions

    @objc private func openSession(_ sender: NSMenuItem) {
        dashboard.show(focus: sender.representedObject as? String)
    }

    @objc private func openDashboard() { dashboard.show() }

    @objc private func openOnboarding() { showOnboarding() }

    private func showOnboarding() {
        present("onboarding", title: "Thiết lập Fleet",
                OnboardingView(backend: backend) { [weak self] in
                    self?.windows["onboarding"]?.close()
                    Task { await self?.tick() }
                    self?.dashboard.show()
                })
    }

    @objc private func openInvite() {
        Task {
            let local = try? await FleetClient(token: TokenStore.read()).local()
            let url = local?["url"] as? String
            let code = url.flatMap { u in TokenStore.read().map { InviteCode(url: u, token: $0) } }
            present("invite", title: "Mời máy mới", InviteView(code: code))
        }
    }

    @objc private func openSettings() {
        present("settings", title: "Cài đặt Fleet",
                SettingsView(model: SettingsModel(),
                             onInvite: { [weak self] in self?.openInvite() },
                             onCheckUpdate: { [weak self] in self?.checkUpdate() }))
    }

    @objc private func checkUpdate() {
        Task {
            let outcome = await updater.run()
            let alert = NSAlert()
            switch outcome {
            case .upToDate: alert.messageText = "Fleet \(Env.appVersion) là bản mới nhất."
            case .installing(let v): alert.messageText = "Đang cài Fleet \(v)…"; return
            case .failed(let msg): alert.messageText = "Không cập nhật được"; alert.informativeText = msg
            case .notSigned(let msg): alert.messageText = "Chưa có bản cập nhật đã ký"; alert.informativeText = msg
            }
            NSApp.activate(ignoringOtherApps: true)
            alert.runModal()
        }
    }

    @objc private func quit() { NSApp.terminate(nil) }

    private func present<V: View>(_ id: String, title: String, _ view: V) {
        windows[id]?.close()
        let win = NSWindow(contentViewController: NSHostingController(rootView: view))
        win.title = title
        win.styleMask = [.titled, .closable]
        win.isReleasedWhenClosed = false
        win.center()
        windows[id] = win
        NSApp.activate(ignoringOtherApps: true)
        win.makeKeyAndOrderFront(nil)
    }
}
