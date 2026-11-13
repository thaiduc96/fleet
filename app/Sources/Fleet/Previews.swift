import AppKit
import SwiftUI
import FleetCore

/// `Fleet --render-previews <dir>`: draw the SwiftUI windows to PNG files and exit.
/// Lets the UI be checked (e.g. in CI or over SSH) without screen-recording rights.
@MainActor
enum Previews {
    static func render(to dir: URL) {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let sample = InviteCode(url: "https://macbook-pro.tail1234.ts.net", token: String(repeating: "a1b2", count: 12))
        write(OnboardingView(backend: Backend(), onDone: {},
                             tailscale: Tailscale.Status(running: true, dnsName: "macbook-pro.tail1234.ts.net"),
                             checkTailscaleOnAppear: false), "onboarding", dir)
        write(InviteView(code: sample), "invite", dir)
        write(InviteView(code: nil), "invite-missing", dir)
        let settings = SettingsModel()
        settings.label = "🏠 Nhà"
        settings.selfMachine = "home"
        settings.url = "https://macbook-pro.tail1234.ts.net"
        settings.awake = "ac"
        settings.machines = [("work", "💼 Công ty", true, "1.0.0"), ("lap", "💻 Laptop", false, nil)]
        write(SettingsView(model: settings, onInvite: {}, onCheckUpdate: {}, loadOnAppear: false), "settings", dir)
    }

    /// ImageRenderer cannot draw AppKit-backed controls (TextField, Picker, Form…), so host
    /// the view in an off-screen window and cache its display instead.
    private static func write<V: View>(_ view: V, _ name: String, _ dir: URL) {
        let host = NSHostingView(rootView: view)
        host.appearance = NSAppearance(named: .aqua)
        let size = host.fittingSize
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: [.borderless],
                              backing: .buffered, defer: false)
        window.contentView = host
        host.frame = NSRect(origin: .zero, size: size)
        host.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date().addingTimeInterval(0.3))  // let SwiftUI settle
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else {
            print("✗ \(name): could not render")
            return
        }
        host.cacheDisplay(in: host.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else {
            print("✗ \(name): could not encode")
            return
        }
        let url = dir.appendingPathComponent("\(name).png")
        try? png.write(to: url)
        print("✓ \(url.path)")
    }
}
