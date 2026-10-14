import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var web: WebWindowController!
    private var status: StatusMenu!
    private var installer: InstallerWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMainMenu()
        web = WebWindowController()
        status = StatusMenu(open: { [weak self] in self?.showWindow() },
                            scan: { [weak self] in self?.scan() },
                            quit: { NSApp.terminate(nil) })
        web.showWindow(nil)
        web.showMessage("Запускаю Memory Desk… При первом запуске после обновления это может занять несколько минут.")
        Task {
            let installed = await self.prepareHome()
            if !installed {
                await MainActor.run { self.web.showMessage("Memory Desk не установлен. Закройте приложение и откройте его снова, чтобы начать установку.") }
                return
            }
            let ready = await Server.shared.ensureRunning()
            await MainActor.run {
                if ready { self.web.load() } else {
                    self.web.showMessage("Memory Desk не запустился. Журнал: папка программы → logs/app.log")
                }
            }
            status.start()
        }
    }

    /// Папка программы уже есть, или показываем окно установки и ждём его результата.
    private func prepareHome() async -> Bool {
        if Server.shared.knownHome() != nil { return true }
        return await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            DispatchQueue.main.async {
                let window = InstallerWindow(done: { ok in continuation.resume(returning: ok) })
                self.installer = window
                window.show()
            }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }   // живёт в строке меню

    func applicationWillTerminate(_ notification: Notification) { Server.shared.stop() }

    // MARK: адреса memorydesk://… (для «Быстрых команд», закладок и Spotlight)
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls where url.scheme == "memorydesk" {
            switch url.host ?? "" {
            case "scan": scan()
            default: showWindow()       // today, open и всё остальное
            }
        }
    }

    func showWindow() {
        web.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// «Обработать новые записи». Под PIN-блокировкой сервер ответит 423: тогда открываем окно, чтобы ввести PIN.
    func scan() {
        Task {
            guard let result = await Api.call("api/jobs/scan", method: "POST") else { return }
            if result.status == 423 { await MainActor.run { self.showWindow() } }
            status.refresh()
        }
    }

    // MARK: главное меню (без него в окне не работают ⌘C, ⌘V, ⌘A, ⌘Q)
    private func buildMainMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        appMenu.addItem(withTitle: "О программе Memory Desk", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Скрыть Memory Desk", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Выйти", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Правка"); editItem.submenu = edit
        edit.addItem(withTitle: "Отменить", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Повторить", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Вырезать", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Копировать", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Вставить", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Выбрать всё", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        let windowItem = NSMenuItem(); main.addItem(windowItem)
        let window = NSMenu(title: "Окно"); windowItem.submenu = window
        window.addItem(withTitle: "Свернуть", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        window.addItem(withTitle: "Закрыть окно", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        NSApp.mainMenu = main
        NSApp.windowsMenu = window
    }
}
