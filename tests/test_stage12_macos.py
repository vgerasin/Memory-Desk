"""Оболочка приложения macOS: проверки, которые видны без компилятора Swift."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- черновик приложения macOS (не собирался)

def test_macos_draft_is_consistent():
    """Swift здесь недоступен, поэтому проверяется только то, что видно без компилятора."""
    import plistlib
    base = ROOT / "macos"
    info = plistlib.loads((base / "Resources/Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "MemoryDesk" and info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["memorydesk"]
    sources = sorted((base / "Sources/MemoryDesk").glob("*.swift"))
    assert {p.name for p in sources} == {"main.swift", "AppDelegate.swift", "Server.swift", "WebWindow.swift", "StatusMenu.swift"}
    for path in sources:
        text = path.read_text(encoding="utf-8")
        import re
        stripped = "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in text.splitlines())      # комментарии с символами не считаем
        for open_, close in ("{}", "()", "[]"):
            assert stripped.count(open_) == stripped.count(close), (path.name, open_)
    package = (base / "Package.swift").read_text(encoding="utf-8")
    assert 'name: "MemoryDesk"' in package and "ЧЕРНОВИК" in package
    script = (base / "build_app.sh").read_text(encoding="utf-8")
    assert script.startswith("#!/bin/zsh") and "codesign" in script and "set -euo pipefail" in script
    assert "не собиралось" in (base / "README.md").read_text(encoding="utf-8")
    # сервер приложения запускает тот же скрипт, что и start.command, и порт по умолчанию совпадает
    server = (base / "Sources/MemoryDesk/Server.swift").read_text(encoding="utf-8")
    assert "meeting-assistant" in server and "8502" in server
    assert (ROOT / "meeting-assistant").exists() and "MEETING_WEBUI_PORT" in (ROOT / "meeting-assistant").read_text(encoding="utf-8")

