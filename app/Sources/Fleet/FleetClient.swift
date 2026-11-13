import Foundation
import FleetCore

/// Talks to fleetd over HTTP. `base` is this Mac's fleetd unless given (joining uses a peer's URL).
struct FleetClient {
    var base: URL = Env.baseURL
    var token: String?

    struct APIError: LocalizedError {
        let status: Int
        let detail: String
        var errorDescription: String? { detail.isEmpty ? "HTTP \(status)" : detail }
    }

    private func request(_ method: String, _ path: String, body: [String: Any]? = nil,
                         timeout: TimeInterval = 15) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = method
        req.timeoutInterval = timeout
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if method != "GET" { req.setValue("1", forHTTPHeaderField: "X-Fleet-Request") }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw APIError(status: status, detail: detail ?? String(decoding: data.prefix(300), as: UTF8.self))
        }
        return data
    }

    func json(_ method: String, _ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        let data = try await request(method, path, body: body)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    struct Hello: Decodable { let fleet: Bool; let machine: String; let label: String; let apiLevel: Int? }

    func hello() async throws -> Hello {
        try JSONDecoder().decode(Hello.self, from: try await request("GET", "api/hello", timeout: 6))
    }

    func fleet() async throws -> FleetSummary {
        try JSONDecoder().decode(FleetSummary.self, from: try await request("GET", "api/fleet", timeout: 20))
    }

    func local() async throws -> [String: Any] { try await json("GET", "api/local") }

    func settings(label: String? = nil, url: String? = nil) async throws {
        var body: [String: Any] = [:]
        if let label { body["label"] = label }
        if let url { body["url"] = url }
        _ = try await json("POST", "api/settings", body: body)
    }

    func setAwake(_ mode: String) async throws { _ = try await json("POST", "api/awake", body: ["mode": mode]) }

    /// Introduce `machine` to the fleetd at `base`; returns that machine's roster.
    func join(machine: String, label: String, url: String) async throws -> [String: Any] {
        try await json("POST", "api/peers/join", body: ["machine": machine, "label": label, "url": url])
    }

    func removePeerEverywhere(_ machine: String) async throws {
        var comps = URLComponents(url: base.appendingPathComponent("api/peers/\(machine)"), resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "everywhere", value: "true")]
        var req = URLRequest(url: comps.url!)
        req.httpMethod = "DELETE"
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        req.setValue("1", forHTTPHeaderField: "X-Fleet-Request")
        let (_, resp) = try await URLSession.shared.data(for: req)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw APIError(status: status, detail: "") }
    }
}
