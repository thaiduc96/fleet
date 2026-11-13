import Foundation

/// The bits of Tailscale Fleet needs: is it running, what is this Mac's MagicDNS
/// name, and is `tailscale serve` forwarding HTTPS to fleetd.
enum Tailscale {
    struct Status {
        let running: Bool
        let dnsName: String?   // "macbook-pro.tail1234.ts.net"
        var url: String? { dnsName.map { "https://\($0)" } }
    }

    static var cli: String? {
        ["/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
         "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    static var installed: Bool { cli != nil }

    static func status() -> Status {
        guard let cli else { return Status(running: false, dnsName: nil) }
        let r = Shell.run(cli, ["status", "--json"], timeout: 10)
        guard r.ok, let data = r.stdout.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return Status(running: false, dnsName: nil)
        }
        let running = (obj["BackendState"] as? String) == "Running"
        var dns = ((obj["Self"] as? [String: Any])?["DNSName"] as? String) ?? ""
        while dns.hasSuffix(".") { dns.removeLast() }
        return Status(running: running, dnsName: dns.isEmpty ? nil : dns)
    }

    /// Whether `tailscale serve` already proxies to our port.
    static func serving(port: Int) -> Bool {
        guard let cli else { return false }
        let r = Shell.run(cli, ["serve", "status"], timeout: 10)
        return r.ok && r.stdout.contains("127.0.0.1:\(port)")
    }

    /// `tailscale serve --bg <port>`; returns an error message, or nil on success.
    /// Fails with a link when Serve is not enabled for the tailnet yet.
    static func serve(port: Int) -> String? {
        guard let cli else { return "Chưa cài Tailscale" }
        let r = Shell.run(cli, ["serve", "--bg", String(port)], timeout: 20)
        if r.ok, serving(port: port) { return nil }
        let text = (r.stdout + r.stderr).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? "tailscale serve thất bại" : text
    }
}
