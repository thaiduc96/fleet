import Foundation

/// Run a command-line tool and collect its output. Blocking: never call it on the
/// main thread (use `Shell.runAsync` from UI code).
struct Shell {
    struct Result {
        let status: Int32
        let stdout: String
        let stderr: String
        var ok: Bool { status == 0 }
    }

    @discardableResult
    static func run(_ path: String, _ args: [String], input: String? = nil, timeout: TimeInterval = 30) -> Result {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        let inPipe = input.map { _ in Pipe() }
        p.standardInput = inPipe ?? FileHandle.nullDevice
        do { try p.run() } catch {
            return Result(status: -1, stdout: "", stderr: "\(error)")
        }
        if let inPipe, let input {
            inPipe.fileHandleForWriting.write(Data(input.utf8))
            try? inPipe.fileHandleForWriting.close()
        }
        // Drain both pipes concurrently so a chatty tool cannot fill one and block.
        var outData = Data(), errData = Data()
        let group = DispatchGroup()
        group.enter(); DispatchQueue.global().async { outData = out.fileHandleForReading.readDataToEndOfFile(); group.leave() }
        group.enter(); DispatchQueue.global().async { errData = err.fileHandleForReading.readDataToEndOfFile(); group.leave() }
        let exited = DispatchSemaphore(value: 0)
        DispatchQueue.global().async { p.waitUntilExit(); exited.signal() }
        if exited.wait(timeout: .now() + timeout) == .timedOut {
            p.terminate()
            _ = exited.wait(timeout: .now() + 2)
        }
        _ = group.wait(timeout: .now() + 2)
        return Result(status: p.isRunning ? -1 : p.terminationStatus,
                      stdout: String(decoding: outData, as: UTF8.self),
                      stderr: String(decoding: errData, as: UTF8.self))
    }

    /// `run` on a background thread, for callers on the main actor.
    static func runAsync(_ path: String, _ args: [String], input: String? = nil,
                         timeout: TimeInterval = 30) async -> Result {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                cont.resume(returning: run(path, args, input: input, timeout: timeout))
            }
        }
    }

    /// Run any blocking closure off the main thread.
    static func background<T: Sendable>(_ work: @escaping @Sendable () -> T) async -> T {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async { cont.resume(returning: work()) }
        }
    }

    /// Start a helper that must outlive us (own session → launchd will not kill it
    /// with our process group when the app exits).
    static func spawnDetached(_ path: String, _ args: [String]) -> Bool {
        var attr: posix_spawnattr_t?
        posix_spawnattr_init(&attr)
        defer { posix_spawnattr_destroy(&attr) }
        posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETSID))
        let argv: [UnsafeMutablePointer<CChar>?] = ([path] + args).map { strdup($0) } + [nil]
        defer { argv.forEach { free($0) } }
        var pid: pid_t = 0
        return posix_spawn(&pid, path, nil, &attr, argv, environ) == 0
    }
}
