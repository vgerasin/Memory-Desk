import AppKit
import Foundation

/// Окно установки: если папки с программой нет, приложение само скачивает проект в ~/memory-desk и запускает
/// `install.command --skip-models` (через `bootstrap.sh`, который лежит внутри приложения). Модели не скачиваются:
/// их по кнопке и с размерами загружает мастер первого запуска в самом кабинете.
final class InstallerWindow: NSObject, NSWindowDelegate {
    static var defaultHome: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("memory-desk") }

    private let window: NSWindow
    private let log = NSTextView()
    private let statusLine = NSTextField(labelWithString: "")
    private let installButton = NSButton(title: "Установить", target: nil, action: nil)
    private let folderButton = NSButton(title: "Выбрать готовую папку…", target: nil, action: nil)
    private let brewButton = NSButton(title: "Открыть brew.sh", target: nil, action: nil)
    private var task: Process?
    private var finished = false
    private let done: (Bool) -> Void

    init(done: @escaping (Bool) -> Void) {
        self.done = done
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 460), styleMask: [.titled, .closable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "Установка Memory Desk"
        window.delegate = self
        window.isReleasedWhenClosed = false
        build()
    }

    func show() {
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func build() {
        guard let content = window.contentView else { return }
        let intro = NSTextField(wrappingLabelWithString:
            "Memory Desk ещё не установлен на этом Mac. Установщик скачает программу в папку ~/memory-desk и подготовит всё нужное: "
            + "Python, ffmpeg и Ollama через Homebrew. Модели (несколько ГБ) скачаются позже, в мастере первого запуска, по вашей кнопке.")
        intro.frame = NSRect(x: 20, y: 380, width: 600, height: 64)
        content.addSubview(intro)

        let scroll = NSScrollView(frame: NSRect(x: 20, y: 90, width: 600, height: 280))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        log.frame = NSRect(x: 0, y: 0, width: 600, height: 280)
        log.isEditable = false
        log.isVerticallyResizable = true
        log.autoresizingMask = [.width]
        log.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        scroll.documentView = log
        content.addSubview(scroll)

        statusLine.frame = NSRect(x: 20, y: 60, width: 600, height: 20)
        content.addSubview(statusLine)

        installButton.frame = NSRect(x: 500, y: 16, width: 120, height: 32)
        installButton.bezelStyle = .rounded
        installButton.keyEquivalent = "\r"
        installButton.target = self
        installButton.action = #selector(startInstall)
        content.addSubview(installButton)

        folderButton.frame = NSRect(x: 290, y: 16, width: 200, height: 32)
        folderButton.bezelStyle = .rounded
        folderButton.target = self
        folderButton.action = #selector(chooseFolder)
        content.addSubview(folderButton)

        brewButton.frame = NSRect(x: 20, y: 16, width: 140, height: 32)
        brewButton.bezelStyle = .rounded
        brewButton.target = self
        brewButton.action = #selector(openBrew)
        brewButton.isHidden = true
        content.addSubview(brewButton)
    }

    private func append(_ text: String) {
        let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
                                                         .foregroundColor: NSColor.labelColor]
        log.textStorage?.append(NSAttributedString(string: text, attributes: attributes))
        log.scrollToEndOfDocument(nil)
        if text.contains("Homebrew") { brewButton.isHidden = false }
    }

    @objc private func startInstall() {
        guard task == nil else { return }
        guard let script = Bundle.main.path(forResource: "bootstrap", ofType: "sh") else {
            statusLine.stringValue = "В приложении нет bootstrap.sh: соберите его заново через build_app.sh."
            return
        }
        installButton.isEnabled = false
        folderButton.isEnabled = false
        statusLine.stringValue = "Идёт установка. Это может занять несколько минут; Нужен интернет."
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = [script, "--skip-models", "--no-open"]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        env["MEMORY_DESK_DIR"] = InstallerWindow.defaultHome.path
        process.environment = env
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.append(text) }
        }
        process.terminationHandler = { [weak self] finishedProcess in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async { self?.installFinished(status: finishedProcess.terminationStatus) }
        }
        do {
            try process.run()
            task = process
        } catch {
            statusLine.stringValue = "Не удалось запустить установщик: \(error.localizedDescription)"
            installButton.isEnabled = true
            folderButton.isEnabled = true
        }
    }

    private func installFinished(status: Int32) {
        task = nil
        if status == 0 {
            UserDefaults.standard.set(InstallerWindow.defaultHome.path, forKey: Server.defaultsKey)
            finish(true)
        } else {
            statusLine.stringValue = "Установка не завершилась (код \(status)). Причина выше; журнал: ~/memory-desk/logs/install.log"
            installButton.title = "Повторить"
            installButton.isEnabled = true
            folderButton.isEnabled = true
        }
    }

    @objc private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.title = "Папка Memory Desk"
        panel.message = "Выберите папку программы (в ней лежит файл meeting-assistant)"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        guard FileManager.default.fileExists(atPath: url.path + "/meeting-assistant") else {
            statusLine.stringValue = "В этой папке нет файла meeting-assistant."
            return
        }
        UserDefaults.standard.set(url.path, forKey: Server.defaultsKey)
        finish(true)
    }

    @objc private func openBrew() {
        if let url = URL(string: "https://brew.sh") { NSWorkspace.shared.open(url) }
    }

    private func finish(_ ok: Bool) {
        guard !finished else { return }
        finished = true
        window.orderOut(nil)
        done(ok)
    }

    func windowWillClose(_ notification: Notification) {
        if let running = task, running.isRunning { running.terminate() }
        finish(false)
    }
}
