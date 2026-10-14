import AppKit
import Foundation

/// Папка программы и дочерний процесс сервера. Запускается тот же `meeting-assistant`, что и из «start.command»:
/// он готовит окружение и заканчивается `exec python -m memory_desk`, поэтому остановка процесса останавливает сервер.
final class Server {
    static let shared = Server()
    static let defaultsKey = "MemoryDeskHome"
    let port: Int = Int(ProcessInfo.processInfo.environment["MEETING_WEBUI_PORT"] ?? "") ?? 8502
    private var process: Process?
    private(set) var startedByApp = false

    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }

    /// Папка с программой: запоминается; при первом запуске спрашивается один раз.
    func home() -> URL? {
        if let saved = UserDefaults.standard.string(forKey: Server.defaultsKey),
           FileManager.default.fileExists(atPath: saved + "/meeting-assistant") {
            return URL(fileURLWithPath: saved)
        }
        let panel = NSOpenPanel()
        panel.title = "Папка Memory Desk"
        panel.message = "Выберите папку программы (в ней лежит файл meeting-assistant)"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url,
              FileManager.default.fileExists(atPath: url.path + "/meeting-assistant") else { return nil }
        UserDefaults.standard.set(url.path, forKey: Server.defaultsKey)
        return url
    }

    /// Папка программы, если она уже известна: сохранённая или стандартная ~/memory-desk (без вопросов).
    func knownHome() -> URL? {
        if let saved = UserDefaults.standard.string(forKey: Server.defaultsKey),
           FileManager.default.fileExists(atPath: saved + "/meeting-assistant") {
            return URL(fileURLWithPath: saved)
        }
        let standard = InstallerWindow.defaultHome
        if FileManager.default.fileExists(atPath: standard.path + "/meeting-assistant") {
            UserDefaults.standard.set(standard.path, forKey: Server.defaultsKey)
            return standard
        }
        return nil
    }

    func isRunning() async -> Bool {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/health"))
        request.timeoutInterval = 1.5
        guard let (data, _) = try? await URLSession.shared.data(for: request),
              let text = String(data: data, encoding: .utf8) else { return false }
        return text.contains("indexState")
    }

    /// Поднимает сервер, если он ещё не работает. Возвращает false, если не удалось за `timeout` секунд.
    func ensureRunning(timeout: Int = 300) async -> Bool {
        if await isRunning() { return true }
        guard let home = home() else { return false }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/zsh")
        task.arguments = [home.appendingPathComponent("meeting-assistant").path]
        task.currentDirectoryURL = home
        var env = ProcessInfo.processInfo.environment
        env["MEETING_WEBUI_PORT"] = String(port)
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")   // у приложения из Finder PATH короткий
        task.environment = env
        let logs = home.appendingPathComponent("logs")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let logPath = logs.appendingPathComponent("app.log")
        FileManager.default.createFile(atPath: logPath.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: logPath) {
            task.standardOutput = handle
            task.standardError = handle
        }
        do { try task.run() } catch { return false }
        process = task
        startedByApp = true
        for _ in 0..<timeout {
            if await isRunning() { return true }
            if !task.isRunning { return false }
            try? await Task.sleep(nanoseconds: 1_000_000_000)
        }
        return false
    }

    /// Останавливает только тот сервер, который запустило приложение; чужой (из «start.command») не трогает.
    func stop() {
        guard startedByApp, let task = process, task.isRunning else { return }
        task.terminate()
        let deadline = Date().addingTimeInterval(5)
        while task.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
        if task.isRunning { kill(task.processIdentifier, SIGKILL) }
    }
}

/// Короткие обращения к API кабинета с нужным заголовком. Под PIN-блокировкой ответ 423 — об этом сказано в README.
enum Api {
    static func call(_ path: String, method: String = "GET") async -> (status: Int, data: Data)? {
        var request = URLRequest(url: Server.shared.baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 5
        if method != "GET" {
            request.setValue("1", forHTTPHeaderField: "X-Memory-Desk")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = Data("{}".utf8)
        }
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse else { return nil }
        return (http.statusCode, data)
    }
}
