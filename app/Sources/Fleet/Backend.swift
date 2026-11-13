import Foundation

/// Runs fleetd (the bundled Python) as a child process and keeps it alive.
///
/// Because Fleet.app is the responsible process, macOS privacy prompts (Documents,
/// Local Network, …) are shown for "Fleet" instead of silently blocking a
/// background python, which is what hung the work Mac with the old LaunchAgent.
///
/// All process bookkeeping happens on a private serial queue; the UI reads `mode`,
/// a copy published on the main thread.
final class Backend {
    enum Mode: Equatable { case stopped, starting, child, external, unavailable(String) }

    /// Main-thread copy of the state (read this from UI code only).
    @MainActor private(set) var mode: Mode = .stopped
    @MainActor var onChange: ((Mode) -> Void)?

    private let queue = DispatchQueue(label: "fleet.backend")
    private var process: Process?   // queue-confined
    private var stopping = false
    private var restarting = false
    private var failures = 0

    func start() { queue.async { self.launch() } }

    /// Restart the child now (not counted as a crash). Starts it if it was not running.
    func restart() {
        queue.async {
            guard let p = self.process, p.isRunning else { self.launch(); return }
            self.restarting = true
            p.terminate()  // exited() relaunches immediately
        }
    }

    /// Stop and wait (≤5 s) for fleetd to exit. Safe to call from the main thread at quit.
    func stop() {
        queue.sync {
            stopping = true
            if let p = process, p.isRunning {
                p.terminate()
                let deadline = Date().addingTimeInterval(5)
                while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
                if p.isRunning { kill(p.processIdentifier, SIGKILL) }
            }
            process = nil
            publish(.stopped)
        }
    }

    private func publish(_ m: Mode) {
        DispatchQueue.main.async {
            MainActor.assumeIsolated {
                self.mode = m
                self.onChange?(m)
            }
        }
    }

    enum PortOwner { case nobody, fleet, other }

    /// Who answers on our port: nobody, a fleetd (`/api/hello` says fleet:true), or something else.
    static func portOwner() -> PortOwner {
        var req = URLRequest(url: Env.baseURL.appendingPathComponent("api/hello"))
        req.timeoutInterval = 2
        let sem = DispatchSemaphore(value: 0)
        var owner = PortOwner.nobody
        URLSession.shared.dataTask(with: req) { data, resp, error in
            if error == nil, resp != nil {
                let hello = data.flatMap { try? JSONDecoder().decode(FleetClient.Hello.self, from: $0) }
                owner = (resp as? HTTPURLResponse)?.statusCode == 200 && hello?.fleet == true ? .fleet : .other
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 3)
        return owner
    }

    private func command() -> (URL, [String])? {
        if let custom = ProcessInfo.processInfo.environment["FLEET_DEV_BACKEND"], !custom.isEmpty {
            return (URL(fileURLWithPath: custom), [])  // e.g. a venv's fleetd script, for development
        }
        // -I: ignore user site/env; -B: never write .pyc into the signed bundle.
        if let py = Env.bundledPython { return (py, ["-I", "-B", "-m", "fleetd.main"]) }
        return nil
    }

    private func launch() {
        stopping = false
        if process?.isRunning == true { return }
        switch Self.portOwner() {
        case .fleet:
            publish(.external)  // another fleetd (old install still stopping, or a dev server)
            return
        case .other:
            publish(.unavailable("Cổng \(Env.port) đang bị chương trình khác dùng"))
            queue.asyncAfter(deadline: .now() + 10) { [weak self] in self?.launch() }
            return
        case .nobody:
            break
        }
        guard let (exe, args) = command() else {
            publish(.unavailable("Không tìm thấy fleetd trong app"))
            return
        }
        try? FileManager.default.createDirectory(at: Env.fleetHome, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: Env.logFile.path) {
            FileManager.default.createFile(atPath: Env.logFile.path, contents: nil)
        }
        let log = try? FileHandle(forWritingTo: Env.logFile)
        log?.seekToEndOfFile()

        let p = Process()
        p.executableURL = exe
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["FLEET_APP_VERSION"] = Env.appVersion
        env["FLEET_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        env["PYTHONUNBUFFERED"] = "1"
        let extra = ["\(Env.home.path)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"]
        env["PATH"] = (extra + [env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"]).joined(separator: ":")
        p.environment = env
        p.currentDirectoryURL = Env.fleetHome
        p.standardOutput = log
        p.standardError = log
        p.standardInput = FileHandle.nullDevice
        p.terminationHandler = { [weak self] proc in
            self?.queue.async { self?.exited(proc) }
        }
        do {
            try p.run()
            process = p
            publish(.child)
        } catch {
            publish(.unavailable("Không chạy được fleetd: \(error.localizedDescription)"))
        }
    }

    private func exited(_ p: Process) {
        guard p === process else { return }  // an older child; already replaced
        process = nil
        if stopping { return }
        if restarting {
            restarting = false
            failures = 0
            launch()
            return
        }
        failures = p.terminationStatus == 0 ? 0 : failures + 1
        let delay = min(30.0, pow(2.0, Double(min(failures, 5))))  // 2s … 30s backoff
        publish(.unavailable("fleetd dừng (mã \(p.terminationStatus)); chạy lại sau \(Int(delay))s"))
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in self?.launch() }
    }
}
