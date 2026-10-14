"""Токены доступа для командной строки и MCP: отдельный ключ на каждого клиента, отзывается в один клик.

Токен — длинная случайная строка вида `mdt_…`. Показывается один раз при создании; на диске (`access-tokens.json`, права
только для владельца) остаётся хэш, поэтому утечка файла токены не раскрывает. Токен не заменяет PIN кабинета и не даёт
интерфейса: он открывает только короткий список маршрутов (`ROUTES`), чтение или чтение с записью, а каждое обращение
попадает в журнал действий под именем клиента («Командная строка» или «MCP»).

Что может токен только для чтения: искать по записям, открыть запись, посмотреть задачи и решения, досье человека
или проекта, дайджест за период, задать вопрос по записям, увидеть очередь обработки. Что добавляет токен с записью:
положить аудиофайл в очередь обработки и добавить текстовую заметку к записи. Править, удалять и выгружать записи
токен не может в любом случае.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone

from . import config

PREFIX = "mdt_"
KINDS = {"cli": "Командная строка", "mcp": "MCP"}
MAX_TOKENS = 20
NAME_MAX = 60
_lock = threading.Lock()

# (метод, шаблон пути) -> нужна ли запись. Путь сопоставляется целиком; `*` — один сегмент без «/».
ROUTES: list[tuple[str, str, bool]] = [
    ("GET", "/api/health", False),
    ("GET", "/api/access/me", False),
    ("GET", "/api/search", False),
    ("GET", "/api/meetings", False),
    ("GET", "/api/meetings/*", False),
    ("GET", "/api/tasks", False),
    ("GET", "/api/people", False),
    ("GET", "/api/people/*", False),
    ("GET", "/api/projects", False),
    ("GET", "/api/projects/*", False),
    ("GET", "/api/prep", False),
    ("GET", "/api/jobs", False),
    ("POST", "/api/chat", False),
    ("POST", "/api/materials/digest", False),
    ("POST", "/api/ingest", True),
    ("POST", "/api/jobs/scan", True),
    ("POST", "/api/meetings/*/attachments/text", True),
]
_PATTERNS = [(m, re.compile("^" + re.escape(p).replace(r"\*", "[^/]+") + "$"), w) for m, p, w in ROUTES]


def path():
    return config.DATA_DIR / "access-tokens.json"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load() -> list[dict]:
    try:
        data = json.loads(path().read_text("utf-8"))
        return [r for r in data if isinstance(r, dict) and r.get("hash")]
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, file)


class AccessError(ValueError):
    pass


def public(row: dict) -> dict:
    """То, что видно в интерфейсе: без хэша."""
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "kindLabel": KINDS.get(row["kind"], row["kind"]),
            "write": bool(row.get("write")), "created": row.get("created", ""), "lastUsed": row.get("last_used", ""),
            "hint": row.get("hint", "")}


def listing() -> list[dict]:
    return [public(r) for r in _load()]


def create(name: str, kind: str, write: bool = False) -> tuple[dict, str]:
    name = " ".join((name or "").split())
    if kind not in KINDS:
        raise AccessError("Неизвестный вид клиента: нужен «cli» или «mcp»")
    if not name:
        name = KINDS[kind]
    if len(name) > NAME_MAX:
        raise AccessError(f"Название длиннее {NAME_MAX} знаков")
    token = PREFIX + secrets.token_urlsafe(32)
    with _lock:
        rows = _load()
        if len(rows) >= MAX_TOKENS:
            raise AccessError(f"Уже выпущено {MAX_TOKENS} токенов: отзовите ненужные")
        row = {"id": secrets.token_hex(6), "name": name, "kind": kind, "write": bool(write), "hash": _hash(token),
               "hint": token[:8] + "…" + token[-4:], "created": _now(), "last_used": ""}
        rows.append(row)
        _save(rows)
    return public(row), token


def revoke(token_id: str) -> bool:
    with _lock:
        rows = _load()
        kept = [r for r in rows if r["id"] != token_id]
        if len(kept) == len(rows):
            return False
        _save(kept)
    return True


def verify(token: str) -> dict | None:
    """Строка токена или None. Сравнение хэшей — за постоянное время."""
    if not token or not token.startswith(PREFIX) or len(token) > 200:
        return None
    digest = _hash(token)
    found = None
    for row in _load():
        if hmac.compare_digest(row["hash"], digest):
            found = row
    return found


def touch(token_id: str) -> None:
    """Отметка «использован»: не чаще раза в минуту на токен, чтобы не переписывать файл на каждый запрос."""
    with _lock:
        rows = _load()
        for row in rows:
            if row["id"] == token_id:
                last = row.get("last_used", "")
                now = _now()
                if last[:16] != now[:16]:
                    row["last_used"] = now
                    _save(rows)
                return


def bearer(header: str) -> str:
    match = re.match(r"^\s*Bearer\s+(\S+)\s*$", header or "", re.I)
    return match.group(1) if match else ""


def route_rule(method: str, path_: str) -> tuple[bool, bool]:
    """(маршрут разрешён токенам вообще, нужна ли запись)."""
    for m, pattern, write in _PATTERNS:
        if m == method and pattern.match(path_):
            return True, write
    return False, False
