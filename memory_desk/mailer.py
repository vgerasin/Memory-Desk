"""Черновик письма в приложении «Почта» macOS. Письмо никогда не отправляется: окно с черновиком открывается, дальше
текст проверяет и отправляет сам человек.

Доступ: при первом вызове macOS спросит разрешение «Memory Desk хочет управлять приложением Почта».
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading


class MailError(RuntimeError):
    """Не удалось подготовить черновик; текст понятен пользователю."""


_SCRIPT = '''on run argv
  set theSubject to item 1 of argv
  set theBody to item 2 of argv
  tell application "Mail"
    set msg to make new outgoing message with properties {subject:theSubject, content:theBody, visible:true}
    tell msg
      repeat with i from 3 to (count of argv)
        make new to recipient at end of to recipients with properties {address:(item i of argv)}
      end repeat
    end tell
    activate
  end tell
end run'''


class MacMail:
    def available(self) -> bool:
        return sys.platform == "darwin" and shutil.which("osascript") is not None

    def draft(self, subject: str, body: str, to: list[str] | None = None) -> dict:
        if not self.available():
            raise MailError("Черновики писем доступны только на macOS")
        try:
            done = subprocess.run(["osascript", "-e", _SCRIPT, subject, body, *(to or [])],
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise MailError(f"Не удалось запустить «Почту»: {exc}") from exc
        if done.returncode != 0:
            reason = (done.stderr or "").strip().splitlines()[-1:] or ["неизвестная ошибка"]
            raise MailError("«Почта» не создала черновик: " + reason[0] +
                            ". Проверьте «Системные настройки → Конфиденциальность → Автоматизация».")
        return {"to": list(to or []), "subject": subject}


class UnavailableMail:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def available(self) -> bool:
        return False

    def draft(self, *_a, **_k):
        raise MailError(self.reason)


class FakeMail:
    """Для тестов: запоминает черновики и ничего не отправляет."""

    def __init__(self) -> None:
        self.drafts: list[dict] = []
        self.sent: list[dict] = []     # всегда пуст: отправки нет в интерфейсе вовсе

    def available(self) -> bool:
        return True

    def draft(self, subject: str, body: str, to: list[str] | None = None) -> dict:
        item = {"to": list(to or []), "subject": subject, "body": body}
        self.drafts.append(item)
        return item


_backend = None
_lock = threading.Lock()


def backend():
    global _backend
    with _lock:
        if _backend is None:
            if os.environ.get("MEMORY_DESK_FAKE_APPLE"):
                _backend = FakeMail()
            elif sys.platform != "darwin":
                _backend = UnavailableMail("Черновики писем доступны только на macOS")
            else:
                _backend = MacMail()
        return _backend


def use_backend(new) -> None:
    global _backend
    with _lock:
        _backend = new
