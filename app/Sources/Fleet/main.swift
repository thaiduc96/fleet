import AppKit

// Fleet.app: menu bar app that runs fleetd, shows the dashboard, pairs Macs and updates itself.
let args = CommandLine.arguments
if let i = args.firstIndex(of: "--render-previews"), i + 1 < args.count {
    MainActor.assumeIsolated { Previews.render(to: URL(fileURLWithPath: args[i + 1])) }
    exit(0)
}
let app = NSApplication.shared
let delegate = MainActor.assumeIsolated { AppDelegate() }
app.delegate = delegate
app.run()
