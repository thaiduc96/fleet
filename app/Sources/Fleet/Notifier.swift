import Foundation
import UserNotifications
import FleetCore

/// Native notifications when a session needs you, finishes, or fails — on any machine.
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    private var previous: [String: String]?
    private var enabled = false
    var onOpen: ((String) -> Void)?   // "machine/id"

    func setUp() {
        // UNUserNotificationCenter requires a real bundle; `swift run` has none.
        guard Env.managesSystem else { return }
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { [weak self] granted, _ in
            self?.enabled = granted
        }
    }

    private static let labels = ["needs_input": "cần bạn trả lời", "done": "xong", "failed": "lỗi"]

    func observe(_ summary: FleetSummary) {
        defer { previous = summary.states }
        guard enabled, let previous else { return }
        for item in summary.transitions(from: previous, into: Set(Self.labels.keys)) {
            let content = UNMutableNotificationContent()
            content.title = "\(item.machineLabel): \(Self.labels[item.session.state] ?? item.session.state)"
            content.body = [item.session.name, item.session.detail].compactMap { $0 }.joined(separator: "\n")
            content.userInfo = ["key": item.id]
            if item.session.state == "needs_input" { content.sound = .default }
            let req = UNNotificationRequest(identifier: item.id + "-" + item.session.state, content: content, trigger: nil)
            UNUserNotificationCenter.current().add(req)
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        if let key = response.notification.request.content.userInfo["key"] as? String {
            DispatchQueue.main.async { self.onOpen?(key) }
        }
        done()
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent n: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound])
    }
}
