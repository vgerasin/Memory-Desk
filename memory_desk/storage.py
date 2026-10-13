"""SQLite: рабочие статусы, связи и реестры. Obsidian остаётся источником заметок."""
from __future__ import annotations

import re
import shutil
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import config

_init_lock = threading.Lock()
_initialized_for: Path | None = None


def db_path() -> Path:
    return config.DATA_DIR / "meetings.db"


def backup_dir() -> Path:
    return config.DATA_DIR / "backups"


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = path or db_path()      # путь читается один раз: папка данных не должна смениться между созданием и открытием
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize() -> None:
    global _initialized_for
    with _init_lock:
        target = db_path()
        if _initialized_for == target:
            return
        with connect(target) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
                recorded_at TEXT, meeting_date TEXT, meeting_time TEXT, duration TEXT,
                project_id TEXT, project_title TEXT, record_type_id TEXT, record_type_title TEXT,
                processing_status TEXT NOT NULL DEFAULT 'ready', review_status TEXT NOT NULL DEFAULT 'needs_review',
                important INTEGER NOT NULL DEFAULT 0, source_audio TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                text TEXT NOT NULL, owner TEXT, evidence_time TEXT, status TEXT NOT NULL DEFAULT 'draft',
                replaced_by TEXT, updated_at TEXT NOT NULL, UNIQUE(meeting_id, text)
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                text TEXT NOT NULL, owner TEXT, deadline TEXT, evidence_time TEXT,
                status TEXT NOT NULL DEFAULT 'draft', updated_at TEXT NOT NULL, UNIQUE(meeting_id, text)
            );
            CREATE TABLE IF NOT EXISTS people (
                id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, role TEXT, notes TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, title TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                description TEXT, goal TEXT, path TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meeting_people (
                meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                PRIMARY KEY(meeting_id, person_id)
            );
            CREATE TABLE IF NOT EXISTS processing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT, event TEXT NOT NULL,
                detail TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reminder_links (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                reminder_id TEXT, list_title TEXT, fingerprint TEXT,
                task_done INTEGER NOT NULL DEFAULT 0, reminder_done INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'active', updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS voice_profiles (
                person_id TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
                embedding BLOB NOT NULL, samples INTEGER NOT NULL DEFAULT 1,
                seconds REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meeting_voices (
                meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                label TEXT NOT NULL, embedding BLOB NOT NULL, seconds REAL NOT NULL DEFAULT 0,
                matched INTEGER NOT NULL DEFAULT 0, score REAL,
                PRIMARY KEY(meeting_id, label)
            );
            CREATE TABLE IF NOT EXISTS suggestions (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', title TEXT NOT NULL,
                day TEXT NOT NULL DEFAULT '', clock TEXT NOT NULL DEFAULT '', all_day INTEGER NOT NULL DEFAULT 0,
                who TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', quote TEXT NOT NULL DEFAULT '',
                evidence_time TEXT NOT NULL DEFAULT '', sec REAL NOT NULL DEFAULT 0, confidence REAL NOT NULL DEFAULT 0,
                engine TEXT NOT NULL DEFAULT '', deadline INTEGER NOT NULL DEFAULT 0, ambiguous INTEGER NOT NULL DEFAULT 0,
                edited INTEGER NOT NULL DEFAULT 0, created_ref TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS suggestions_meeting ON suggestions(meeting_id);
            CREATE TABLE IF NOT EXISTS dossier_pins (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, owner_id TEXT NOT NULL, text TEXT NOT NULL,
                origin TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'manual',
                meeting_id TEXT NOT NULL DEFAULT '', sec REAL, edited INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS dossier_pins_owner ON dossier_pins(scope, owner_id);
            CREATE TABLE IF NOT EXISTS dossier_mutes (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, owner_id TEXT NOT NULL, text TEXT NOT NULL,
                meeting_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS dossier_mutes_owner ON dossier_mutes(scope, owner_id);
            CREATE TABLE IF NOT EXISTS dossier_gists (
                scope TEXT NOT NULL, owner_id TEXT NOT NULL, text TEXT NOT NULL, engine TEXT NOT NULL,
                signature TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
                PRIMARY KEY(scope, owner_id)
            );
            CREATE TABLE IF NOT EXISTS meeting_overlaps (
                meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                sec REAL NOT NULL, PRIMARY KEY(meeting_id, sec)
            );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(meetings)")}
            if "archived" not in columns:
                db.execute("ALTER TABLE meetings ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            people_columns = {row[1] for row in db.execute("PRAGMA table_info(people)")}
            for column in ("organization", "aliases"):
                if column not in people_columns:
                    db.execute(f"ALTER TABLE people ADD COLUMN {column} TEXT")
            for column, ddl in (("calendar_event_id", "TEXT"), ("calendar_event_title", "TEXT"),
                                ("calendar_dismissed", "INTEGER NOT NULL DEFAULT 0")):
                if column not in columns:
                    db.execute(f"ALTER TABLE meetings ADD COLUMN {column} {ddl}")
        _initialized_for = target


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- разбор фактов из заметок

PLACEHOLDERS = {
    "Не зафиксировано.", "Не указано.", "Не выявлено.", "Задачи не зафиксированы.",
    "Явно согласованные следующие шаги не зафиксированы.",
}


def bullet_lines(text: str) -> list[str]:
    result = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- "):
            value = line[2:].strip()
            if value and value not in PLACEHOLDERS:
                result.append(value)
    return result


def parse_fact(value: str) -> dict:
    evidence = re.search(r"⏱\s*(\d{2}:\d{2}:\d{2})", value)
    owner = re.search(r"\*\*ответственный:\*\*\s*([^;]+)", value, re.IGNORECASE)
    deadline = re.search(r"\*\*срок:\*\*\s*([^;`]+)", value, re.IGNORECASE)
    clean = re.sub(r"^(?:-\s*)?\[[ xX]\]\s*", "", value).strip()
    clean = re.sub(r"\s+—\s+\*\*ответственный:.*$", "", clean).strip()
    clean = re.sub(r"[\s;—-]*`?⏱.*$", "", clean).strip()
    return {
        "text": clean,
        "owner": owner.group(1).strip() if owner else "",
        "deadline": deadline.group(1).strip() if deadline else "",
        "evidence_time": evidence.group(1) if evidence else "",
    }


def sync_meetings(meetings, section_loader) -> None:
    initialize()
    now = now_iso()
    with connect() as db:
        for meeting in meetings:
            meeting_id = meeting.id
            review_status = (
                "reviewed" if meeting.project_id and meeting.source_type not in {"", "Не указан"} else "needs_review"
            )
            db.execute("""
                INSERT INTO meetings (
                    id,path,title,recorded_at,meeting_date,meeting_time,duration,project_id,project_title,
                    record_type_id,record_type_title,processing_status,review_status,source_audio,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    path=excluded.path,title=excluded.title,recorded_at=excluded.recorded_at,
                    meeting_date=excluded.meeting_date,meeting_time=excluded.meeting_time,duration=excluded.duration,
                    project_id=excluded.project_id,project_title=excluded.project_title,
                    record_type_id=excluded.record_type_id,record_type_title=excluded.record_type_title,
                    processing_status='ready',source_audio=excluded.source_audio,updated_at=excluded.updated_at
            """, (
                meeting_id, str(meeting.path), meeting.title, meeting.recorded_at, meeting.date, meeting.time,
                meeting.duration, meeting.project_id, meeting.project, meeting.record_type, meeting.source_type,
                "ready", review_status, meeting.source_audio, now,
            ))
            # Если заметку переименовали/перенесли, но id остался прежним — путь уже обновлён выше.
            for chunk in section_loader(meeting):
                section = chunk.get("section", "")
                table = (
                    "decisions" if section == "Предполагаемые решения и договорённости"
                    else "tasks" if section == "Задачи" else None
                )
                if not table:
                    continue
                # После первого импорта реестр становится пользовательским:
                # правки и статусы не перезаписываются очередной синхронизацией заметок.
                if db.execute(f"SELECT 1 FROM {table} WHERE meeting_id=? LIMIT 1", (meeting_id,)).fetchone():
                    continue
                for text in bullet_lines(chunk.get("text", "")):
                    item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{meeting_id}:{table}:{text}"))
                    fact = parse_fact(text)
                    if not fact["text"]:
                        continue
                    if table == "tasks":
                        db.execute(
                            "INSERT OR IGNORE INTO tasks (id,meeting_id,text,owner,deadline,evidence_time,status,updated_at)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (item_id, meeting_id, fact["text"], fact["owner"], fact["deadline"],
                             fact["evidence_time"], "draft", now),
                        )
                    else:
                        db.execute(
                            "INSERT OR IGNORE INTO decisions (id,meeting_id,text,owner,evidence_time,status,updated_at)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (item_id, meeting_id, fact["text"], fact["owner"], fact["evidence_time"], "draft", now),
                        )
            # Участники из заметки импортируются один раз; дальше источник правды — SQLite (правки в интерфейсе).
            if meeting.participants and not db.execute(
                "SELECT 1 FROM meeting_people WHERE meeting_id=? LIMIT 1", (meeting_id,)
            ).fetchone():
                for name in meeting.participants:
                    if name.strip():
                        person = _upsert_person(db, " ".join(name.split()), "")
                        db.execute("INSERT OR IGNORE INTO meeting_people (meeting_id,person_id) VALUES (?,?)",
                                   (meeting_id, person["id"]))


def sync_projects(projects: list[dict]) -> None:
    initialize()
    now = now_iso()
    with connect() as db:
        for project in projects:
            db.execute("""
                INSERT INTO projects (id,title,status,path,updated_at) VALUES (?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,status=excluded.status,
                    path=excluded.path,updated_at=excluded.updated_at
            """, (project["id"], project["title"], project["status"], project["path"], now))


def rows(query: str, params: tuple = ()) -> list[dict]:
    initialize()
    with connect() as db:
        return [dict(row) for row in db.execute(query, params).fetchall()]


def one(query: str, params: tuple = ()) -> dict | None:
    found = rows(query, params)
    return found[0] if found else None


ALLOWED_TABLES = {"decisions", "tasks", "meetings", "people", "projects"}
ALLOWED_FIELDS = {
    "decisions": {"text", "owner", "status", "evidence_time", "replaced_by"},
    "tasks": {"text", "owner", "deadline", "status", "evidence_time"},
    "meetings": {"review_status", "important", "archived", "project_id", "project_title",
                 "record_type_id", "record_type_title", "calendar_event_id", "calendar_event_title", "calendar_dismissed"},
    "people": {"name", "role", "notes", "organization", "aliases"},
    "projects": {"goal", "description", "status"},
}


def update_item(table: str, item_id: str, **fields) -> bool:
    if table not in ALLOWED_TABLES:
        raise ValueError("Неизвестная таблица")
    bad = set(fields) - ALLOWED_FIELDS[table]
    if bad or not fields:
        raise ValueError(f"Недопустимые поля: {', '.join(sorted(bad)) or '—'}")
    initialize()
    fields["updated_at"] = now_iso()
    assignments = ",".join(f"{key}=?" for key in fields)
    with connect() as db:
        cursor = db.execute(f"UPDATE {table} SET {assignments} WHERE id=?", (*fields.values(), item_id))
        return cursor.rowcount > 0


def get_setting(key: str, default: str = "") -> str:
    row = one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    initialize()
    with connect() as db:
        db.execute("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))


def check_integrity(path: Path | None = None) -> tuple[bool, str]:
    """Проверка файла базы (`PRAGMA integrity_check`). Возвращает (цела ли, сообщение)."""
    target = Path(path) if path else db_path()
    if not target.exists():
        return True, "базы ещё нет"
    try:
        db = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=15)
        try:
            result = db.execute("PRAGMA integrity_check").fetchone()
        finally:
            db.close()
    except sqlite3.Error as exc:
        return False, f"не открывается: {exc}"
    return (True, "цела") if result and result[0] == "ok" else (False, str(result[0] if result else "нет ответа")[:200])


def create_backup() -> Path:
    initialize()
    backup_dir().mkdir(parents=True, exist_ok=True)
    stem = f"meetings-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    target = backup_dir() / f"{stem}.db"
    counter = 1
    while target.exists():  # две копии в одну секунду не должны затирать друг друга
        counter += 1
        target = backup_dir() / f"{stem}-{counter}.db"
    # sqlite3 backup API корректно копирует базу в режиме WAL, в отличие от shutil.copy.
    source = sqlite3.connect(db_path())
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target


def list_backups() -> list[Path]:
    return sorted(backup_dir().glob("*.db"), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True) if backup_dir().exists() else []


def prune_backups(keep: int) -> int:
    """Оставляет `keep` последних копий, остальные удаляет. Возвращает число удалённых."""
    removed = 0
    for old in list_backups()[max(1, int(keep)):]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def restore_backup(path: Path) -> None:
    path = Path(path)
    if path.parent != backup_dir() or not path.exists():
        raise ValueError("Резервная копия не найдена")
    create_backup()
    shutil.copy2(path, db_path())


# --------------------------------------------------------------------------- люди

def _upsert_person(db: sqlite3.Connection, name: str, role: str) -> dict:
    folded = name.casefold()
    existing = None
    for row in db.execute("SELECT id,name,role FROM people").fetchall():
        if " ".join(row["name"].split()).casefold() == folded:
            existing = row
            break
    if existing:
        if role:
            db.execute("UPDATE people SET role=?, updated_at=? WHERE id=?", (role, now_iso(), existing["id"]))
        return {"id": existing["id"], "name": existing["name"], "role": role or existing["role"] or ""}
    person_id = person_id_for(name)
    db.execute(
        "INSERT INTO people (id,name,role,updated_at) VALUES (?,?,?,?)",
        (person_id, name, role, now_iso()),
    )
    return {"id": person_id, "name": name, "role": role}


def create_person(name: str, role: str = "") -> dict:
    name = " ".join(name.split())
    if not name:
        raise ValueError("Имя не может быть пустым")
    if len(name) > 120:
        raise ValueError("Имя слишком длинное: не больше 120 знаков")
    initialize()
    with connect() as db:
        return _upsert_person(db, name, " ".join(role.split())[:200])


def person_id_for(name: str) -> str:
    """Идентификатор человека зависит только от имени: карточка в Obsidian и записи находят друг друга без базы."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "plaud-person:" + " ".join(name.split()).casefold()))


def sync_person_cards(cards: list[dict]) -> None:
    """Карточки из Obsidian (`Люди/*.md`) — источник правды: должность, организация, заметка и псевдонимы берутся из файла."""
    initialize()
    with connect() as db:
        for card in cards:
            person = _upsert_person(db, card["name"], "")
            db.execute("UPDATE people SET role=?, organization=?, aliases=?, notes=?, updated_at=? WHERE id=?",
                       (card.get("role", ""), card.get("organization", ""), json.dumps(card.get("aliases", []), ensure_ascii=False),
                        card.get("notes", ""), now_iso(), person["id"]))


def set_meeting_people(meeting_id: str, names: list[str]) -> list[dict]:
    initialize()
    seen: dict[str, str] = {}
    for n in names:
        clean = " ".join(n.split())
        if clean:
            seen.setdefault(clean.casefold(), clean)
    cleaned = list(seen.values())
    with connect() as db:
        people = [_upsert_person(db, name, "") for name in cleaned]
        db.execute("DELETE FROM meeting_people WHERE meeting_id=?", (meeting_id,))
        db.executemany("INSERT INTO meeting_people (meeting_id,person_id) VALUES (?,?)",
                       [(meeting_id, person["id"]) for person in people])
    return people


def add_meeting_people(meeting_id: str, names: list[str]) -> None:
    """Добавляет людей к участникам записи, не трогая уже назначенных."""
    initialize()
    with connect() as db:
        for name in names:
            clean = " ".join(name.split())
            if clean:
                person = _upsert_person(db, clean, "")
                db.execute("INSERT OR IGNORE INTO meeting_people (meeting_id,person_id) VALUES (?,?)",
                           (meeting_id, person["id"]))


def people_by_meeting() -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for row in rows("""
        SELECT mp.meeting_id, p.id, p.name, p.role FROM meeting_people mp
        JOIN people p ON p.id=mp.person_id ORDER BY p.name
    """):
        result.setdefault(row["meeting_id"], []).append({"id": row["id"], "name": row["name"], "role": row["role"] or ""})
    return result


