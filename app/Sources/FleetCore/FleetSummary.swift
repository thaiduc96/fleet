import Foundation

/// The slice of `GET /api/fleet` the menu bar needs. Unknown fields are ignored and
/// missing ones get defaults, so older/newer fleetd versions still decode.
public struct FleetSummary: Decodable, Sendable, Equatable {
    public struct Session: Decodable, Sendable, Equatable, Identifiable {
        public let id: String
        public let sessionId: String?
        public let name: String?
        public let state: String
        public let detail: String?

        enum CodingKeys: String, CodingKey { case id, sessionId, name, state, detail }

        public init(from d: Decoder) throws {
            let c = try d.container(keyedBy: CodingKeys.self)
            id = (try? c.decode(String.self, forKey: .id)) ?? ""
            sessionId = try? c.decode(String.self, forKey: .sessionId)
            name = try? c.decode(String.self, forKey: .name)
            state = (try? c.decode(String.self, forKey: .state)) ?? "unknown"
            detail = try? c.decode(String.self, forKey: .detail)
        }

        public init(id: String, sessionId: String?, name: String?, state: String, detail: String?) {
            self.id = id; self.sessionId = sessionId; self.name = name; self.state = state; self.detail = detail
        }
    }

    public struct Machine: Decodable, Sendable, Equatable, Identifiable {
        public var id: String { machine }
        public let machine: String
        public let label: String
        public let online: Bool
        public let isSelf: Bool
        public let appVersion: String?
        public let sessions: [Session]

        enum CodingKeys: String, CodingKey { case machine, label, online, isSelf, appVersion, sessions }

        public init(from d: Decoder) throws {
            let c = try d.container(keyedBy: CodingKeys.self)
            machine = try c.decode(String.self, forKey: .machine)
            label = (try? c.decode(String.self, forKey: .label)) ?? machine
            online = (try? c.decode(Bool.self, forKey: .online)) ?? false
            isSelf = (try? c.decode(Bool.self, forKey: .isSelf)) ?? false
            appVersion = try? c.decode(String.self, forKey: .appVersion)
            sessions = (try? c.decode([Session].self, forKey: .sessions)) ?? []
        }
    }

    public let machines: [Machine]

    /// A live session together with the machine it runs on.
    public struct Item: Sendable, Equatable, Identifiable {
        public var id: String { "\(machine)/\(session.id)" }
        public let machine: String
        public let machineLabel: String
        public let session: Session
    }

    public func items(in states: Set<String>) -> [Item] {
        machines.filter(\.online).flatMap { m in
            m.sessions.filter { states.contains($0.state) }
                .map { Item(machine: m.machine, machineLabel: m.label, session: $0) }
        }
    }

    public var needsInput: [Item] { items(in: ["needs_input"]) }
    public var working: [Item] { items(in: ["working"]) }

    /// State per "machine/id", used to notice transitions between two polls.
    public var states: [String: String] {
        var out: [String: String] = [:]
        for m in machines where m.online {
            for s in m.sessions { out["\(m.machine)/\(s.id)"] = s.state }
        }
        return out
    }

    /// Sessions whose state changed into one of `interesting` since `previous`.
    public func transitions(from previous: [String: String], into interesting: Set<String>) -> [Item] {
        machines.filter(\.online).flatMap { m in
            m.sessions.compactMap { s in
                let key = "\(m.machine)/\(s.id)"
                guard let before = previous[key], before != s.state, interesting.contains(s.state) else { return nil }
                return Item(machine: m.machine, machineLabel: m.label, session: s)
            }
        }
    }
}
