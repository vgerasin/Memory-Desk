import AppKit
import WebKit

/// Окно с интерфейсом: тот же React, что и в браузере. Ссылки наружу открываются в браузере, файлы скачиваются в «Загрузки».
final class WebWindowController: NSWindowController, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    let webView: WKWebView

    init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()      // язык и тема запоминаются между запусками
        configuration.applicationNameForUserAgent = "MemoryDeskApp"    // по нему интерфейс включает режим окна приложения
        webView = WKWebView(frame: .zero, configuration: configuration)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 900),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        window.title = "Memory Desk"
        window.contentView = webView
        window.setFrameAutosaveName("MemoryDeskMain")
        window.center()
        super.init(window: window)
        webView.navigationDelegate = self
        webView.uiDelegate = self
    }

    required init?(coder: NSCoder) { fatalError("не используется") }

    func load(path: String = "/") {
        webView.load(URLRequest(url: Server.shared.baseURL.appendingPathComponent(path)))
    }

    func showMessage(_ text: String) {
        let escaped = text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        webView.loadHTMLString("<body style='font:16px -apple-system;padding:48px;color:#555'>\(escaped)</body>", baseURL: nil)
    }

    private func isLocal(_ url: URL?) -> Bool {
        url?.host == "127.0.0.1" && url?.port == Server.shared.port
    }

    // MARK: навигация
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, url.scheme?.hasPrefix("http") == true, !isLocal(url) {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        } else if let url = action.request.url, url.scheme == "obsidian" {
            NSWorkspace.shared.open(url)      // «Открыть в Obsidian»
            decisionHandler(.cancel)
        } else if action.shouldPerformDownload {
            decisionHandler(.download)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.lowercased().contains("attachment") || !response.canShowMIMEType ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }     // target="_blank"
        return nil
    }

    // MARK: скачивание
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        var target = folder.appendingPathComponent(suggestedFilename)
        var n = 1
        while FileManager.default.fileExists(atPath: target.path) {
            let base = (suggestedFilename as NSString).deletingPathExtension, ext = (suggestedFilename as NSString).pathExtension
            target = folder.appendingPathComponent(ext.isEmpty ? "\(base) \(n)" : "\(base) \(n).\(ext)")
            n += 1
        }
        completionHandler(target)
    }

    func downloadDidFinish(_ download: WKDownload) {
        NSWorkspace.shared.activateFileViewerSelecting([FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]])
    }

    // MARK: окна JavaScript и выбор файла (вложения)
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.runModal(); completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = message
        alert.addButton(withTitle: "OK"); alert.addButton(withTitle: "Отмена")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.begin { completionHandler($0 == .OK ? panel.urls : nil) }
    }
}
