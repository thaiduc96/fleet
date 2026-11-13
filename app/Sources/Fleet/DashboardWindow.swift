import AppKit
import WebKit

/// The existing web dashboard in a native window, already logged in.
final class DashboardWindow: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?

    func show(focus key: String? = nil) {
        if window == nil { build() }
        load(focus: key)
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    private func build() {
        let config = WKWebViewConfiguration()
        // In-memory only: the token cookie never lands on disk outside the Keychain.
        config.websiteDataStore = .nonPersistent()
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = false
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 760),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable],
                           backing: .buffered, defer: false)
        win.title = "Fleet"
        win.contentView = web
        win.center()
        win.setFrameAutosaveName("FleetDashboard")
        win.isReleasedWhenClosed = false
        win.delegate = self
        window = win
        webView = web
    }

    /// Set the login cookie fleetd expects, then open the page (optionally on a session).
    private func load(focus key: String?) {
        guard let web = webView else { return }
        var comps = URLComponents(url: Env.baseURL, resolvingAgainstBaseURL: false)!
        comps.path = "/"
        if let key { comps.fragment = "s=\(key)" }
        let url = comps.url!
        // Same shape as the cookie fleetd's own /api/login sets: HttpOnly, SameSite=Strict.
        guard let token = TokenStore.read(),
              let cookie = HTTPCookie(properties: [
                .name: "fleet_token", .value: token, .domain: "127.0.0.1", .path: "/",
                .sameSitePolicy: HTTPCookieStringPolicy.sameSiteStrict,
                HTTPCookiePropertyKey("HttpOnly"): "TRUE",
              ]) else {
            web.load(URLRequest(url: url))
            return
        }
        web.configuration.websiteDataStore.httpCookieStore.setCookie(cookie) {
            web.load(URLRequest(url: url))
        }
    }

    /// Only this Mac's fleetd (127.0.0.1:<port>) loads inside the window, which holds the
    /// token cookie. Web links (claude.ai/code, GitHub) open in the default browser;
    /// anything else is refused.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "http", url.host == "127.0.0.1", url.port == Env.port {
            decisionHandler(.allow)
        } else if url.scheme == "about" {
            decisionHandler(.allow)
        } else {
            if url.scheme == "https" { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }

    // target=_blank links
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, url.scheme == "https" { NSWorkspace.shared.open(url) }
        return nil
    }

    // <input type=file> (📎 attachments): WKWebView on macOS shows no picker unless the app does.
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        guard let window else {
            completionHandler(panel.runModal() == .OK ? panel.urls : nil)
            return
        }
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    // window.confirm / alert from the dashboard (Xoá, …)
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Huỷ")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.runModal()
        completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(defaultText ?? "", forType: .string)
        completionHandler(defaultText)
    }
}
