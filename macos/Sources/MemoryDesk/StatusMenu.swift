import AppKit

/// Значок в строке меню: ход обработки, «Обработать новые», окно, выход.
/// Читает только `/api/state`; под PIN-блокировкой сервер отвечает 423, и значок показывает «нет связи».
final class StatusMenu {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let stateLine = NSMenuItem(title: "Запускаю…", action: nil, keyEquivalent: "")
    private var timer: Timer?
    private let onOpen: () -> Void, onScan: () -> Void, onQuit: () -> Void

    init(open: @escaping () -> Void, scan: @escaping () -> Void, quit: @escaping () -> Void) {
        onOpen = open; onScan = scan; onQuit = quit
        item.button?.image = NSImage(systemSymbolName: "text.bubble", accessibilityDescription: "Memory Desk")
        let menu = NSMenu()
        stateLine.isEnabled = false
        menu.addItem(stateLine)
        menu.addItem(.separator())
        menu.addItem(target(NSMenuItem(title: "Открыть Memory Desk", action: #selector(openWindow), keyEquivalent: "")))
        menu.addItem(target(NSMenuItem(title: "Обработать новые записи", action: #selector(scan), keyEquivalent: "")))
        menu.addItem(.separator())
        menu.addItem(target(NSMenuItem(title: "Выйти", action: #selector(quit), keyEquivalent: "")))
        item.menu = menu
    }

    private func target(_ menuItem: NSMenuItem) -> NSMenuItem { menuItem.target = self; return menuItem }
    @objc private func openWindow() { onOpen() }
    @objc private func scan() { onScan() }
    @objc private func quit() { onQuit() }

    func start() {
        DispatchQueue.main.async {
            self.refresh()
            self.timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.refresh() }
        }
    }

    func refresh() {
        Task {
            guard let result = await Api.call("api/state"), result.status == 200,
                  let json = try? JSONSerialization.jsonObject(with: result.data) as? [String: Any] else {
                await MainActor.run { self.stateLine.title = "Нет связи с сервером"; self.item.button?.appearsDisabled = true }
                return
            }
            let queue = (json["queue"] as? [[String: Any]])?.count ?? 0
            let running = json["running"] != nil && !(json["running"] is NSNull)
            let inbox = json["inboxCount"] as? Int ?? 0
            await MainActor.run {
                self.item.button?.appearsDisabled = false
                self.stateLine.title = running ? "Идёт обработка (в очереди: \(queue))" : (inbox > 0 ? "Ждут внимания: \(inbox)" : "Всё обработано")
            }
        }
    }
}
