"""Общий клиент для командной строки и MCP: находит адрес и токен, ходит в кабинет по HTTP.

Только стандартная библиотека, чтобы `memory-desk` работал и без окружения кабинета.

Откуда берутся адрес и токен (первое найденное):
1. переменные `MEMORY_DESK_URL` и `MEMORY_DESK_TOKEN`;
2. файл `~/.memory-desk/client.json` (его пишет `memory-desk login`, права только для владельца).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8502"
START_HINT = "Откройте приложение Memory Desk или запустите ./start.command в папке проекта."


class ClientError(RuntimeError):
    """Ошибка с текстом для человека; `code` — код выхода командной строки."""

    def __init__(self, message: str, code: int = 1, status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


class Offline(ClientError):
    def __init__(self, url: str):
        super().__init__(f"Memory Desk не запущен ({url}). {START_HINT}", 3)
        self.url = url


def config_path() -> Path:
    override = os.environ.get("MEMORY_DESK_CLIENT_CONFIG")
    return Path(override) if override else Path.home() / ".memory-desk" / "client.json"


def load_saved() -> dict:
    try:
        data = json.loads(config_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(url: str, token: str) -> Path:
    file = config_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"url": url, "token": token}, handle, ensure_ascii=False, indent=1)
    os.chmod(file, 0o600)
    return file


def connection() -> tuple[str, str]:
    saved = load_saved()
    url = (os.environ.get("MEMORY_DESK_URL") or saved.get("url") or DEFAULT_URL).rstrip("/")
    token = os.environ.get("MEMORY_DESK_TOKEN") or saved.get("token") or ""
    return url, token


def call(method: str, path: str, params: dict | None = None, body: dict | None = None, data: bytes | None = None,
         token: bool = True, timeout: float = 60, url: str | None = None, auth: str | None = None):
    """Один запрос к кабинету; возвращает разобранный JSON. `data` — сырое тело (загрузка файла)."""
    base, saved_token = connection()
    base = (url or base).rstrip("/")
    secret = auth if auth is not None else saved_token
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    request = urllib.request.Request(base + path + ("?" + query if query else ""), method=method)
    if token:
        if not secret:
            raise ClientError("Нет токена доступа. Создайте его в кабинете: Настройки → Безопасность → Доступ для программ, "
                              "затем выполните: memory-desk login", 4)
        request.add_header("Authorization", "Bearer " + secret)
    payload = data
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    elif data is not None:
        request.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(request, data=payload, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = str(json.loads(exc.read().decode("utf-8")).get("detail", ""))
        except (ValueError, OSError, AttributeError):
            pass
        code = 4 if exc.code in (401, 403) else 1
        raise ClientError(detail or f"Кабинет ответил ошибкой {exc.code}", code, exc.code) from exc
    except urllib.error.URLError as exc:
        raise Offline(base) from exc
    except (TimeoutError, OSError) as exc:
        raise ClientError(f"Кабинет не ответил за {int(timeout)} с", 1) from exc
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError as exc:
        raise ClientError("Кабинет вернул не JSON: возможно, на этом адресе другая программа", 1) from exc
