"""«Стереть запись целиком»: аудио (и в архиве), заметка, стенограмма, вложения, рабочие файлы обработки, строки базы,
фрагменты поискового индекса, волновая форма, копии заметки и (по выбору) строки записи в резервных копиях базы.

Сначала `plan` показывает, что именно будет стёрто, и ничего не меняет. `apply` требует слово-подтверждение. Порядок
рассчитан на сбой посередине: заметка удаляется последней, поэтому недоделанное стирание можно повторить.

Чего приложение стереть не может и об этом предупреждает: копии вне приложения (Time Machine, iCloud, Obsidian Sync,
git в хранилище), напоминания и события календаря, созданные по записи, голос человека в его профиле, если вы его
запоминали по этой записи (профиль усреднён по нескольким записям), и документы, которые вы не отметили.
Файлы удаляются обычным способом, не перезаписываются поверх.
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

from . import attachments, config, knowledge, mirror, storage

WORD = "СТЕРЕТЬ"
DERIVED_FOLDERS = ("Протоколы", "Карты", "Служебные записки", "Брифы", "Письма", "Дайджесты", "Ответы", "Сводки")


class EraseError(RuntimeError):
    """Стирание не выполнено; текст понятен пользователю. `status` — код ответа API."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- что есть у записи

def _audio_files(meeting: knowledge.Meeting) -> list[Path]:
    name = meeting.source_audio
    found: list[Path] = []
    if not name:
        return found
    for root in (knowledge.AUDIO_ROOT, knowledge.ARCHIVE_ROOT):
        if not root.exists():
            continue
        direct = root / name
        candidates = [direct] if direct.is_file() else []
        candidates += [p for p in root.rglob(name) if p.is_file()]
        for path in candidates:
            if path not in found:
                found.append(path)
    return found


def _work_folders(meeting: knowledge.Meeting) -> list[Path]:
    root = config.PROJECT_ROOT / "work"
    if not root.is_dir():
        return []
    target = str(meeting.path)
    found = []
    for folder in root.iterdir():
        state = folder / "state.json"
        if not folder.is_dir() or not state.is_file():
            continue
        try:
            text = state.read_text(encoding="utf-8")
            note = str(json.loads(text).get("note", ""))
        except (OSError, ValueError):
            continue
        if note == target or (note and Path(note).name == meeting.path.name) or (meeting.source_audio and meeting.source_audio in text):
            found.append(folder)
    return found


def _note_copies(meeting: knowledge.Meeting) -> list[Path]:
    folder = config.DATA_DIR / "note-backups"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob(glob.escape(meeting.path.stem) + ".*.md") if p.is_file())


def _documents(meeting: knowledge.Meeting) -> list[Path]:
    """Документы, составленные по записи (протоколы, записки, ответы чата…): находятся по ссылке на заметку или id."""
    found = []
    stem, ident = meeting.path.stem, meeting.id
    for name in DERIVED_FOLDERS:
        folder = knowledge.WORKSPACE_DIR / name
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.md")):
            if path.parent == mirror.folder() and path.name in ("Задачи.md", "Решения.md"):
                continue      # сводки собираются из базы заново сразу после стирания
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if stem in text or ident in text:
                found.append(path)
    return found


def _doc_id(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def _db_counts(meeting_id: str) -> dict[str, int]:
    def count(sql: str) -> int:
        row = storage.one(sql, (meeting_id,))
        return int(row["n"]) if row else 0
    return {
        "tasks": count("SELECT COUNT(*) n FROM tasks WHERE meeting_id=?"),
        "decisions": count("SELECT COUNT(*) n FROM decisions WHERE meeting_id=?"),
        "people": count("SELECT COUNT(*) n FROM meeting_people WHERE meeting_id=?"),
        "voices": count("SELECT COUNT(*) n FROM meeting_voices WHERE meeting_id=?"),
        "overlaps": count("SELECT COUNT(*) n FROM meeting_overlaps WHERE meeting_id=?"),
        "events": count("SELECT COUNT(*) n FROM processing_events WHERE meeting_id=?"),
        "reminders": count("SELECT COUNT(*) n FROM reminder_links r JOIN tasks t ON t.id=r.task_id WHERE t.meeting_id=? AND r.state='active'"),
    }


def _backups_with_record(meeting_id: str) -> list[Path]:
    found = []
    for path in storage.list_backups():
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                if db.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone():
                    found.append(path)
            finally:
                db.close()
        except sqlite3.Error:
            continue
    return found


def plan(core, meeting_id: str) -> dict:
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise EraseError("Запись не найдена", 404)
    audio, transcript = _audio_files(meeting), knowledge.transcript_file(meeting)
    files = attachments.folder(meeting)
    attached = [p for p in files.rglob("*") if p.is_file()] if files.is_dir() else []
    counts = _db_counts(meeting_id)
    copies, documents = _note_copies(meeting), _documents(meeting)
    in_backups = _backups_with_record(meeting_id)
    items = [
        {"key": "audio", "label": "Аудиозапись", "count": len(audio), "names": [p.name for p in audio],
         "empty": "Файла записи нет (возможно, он удалён по сроку хранения)"},
        {"key": "note", "label": "Заметка в Obsidian", "count": 1, "names": [meeting.path.name]},
        {"key": "transcript", "label": "Стенограмма", "count": 1 if transcript else 0, "names": [transcript.name] if transcript else []},
        {"key": "attachments", "label": "Вложения (фото и тексты)", "count": len(attached), "names": [p.name for p in attached]},
        {"key": "work", "label": "Рабочие файлы обработки", "count": len(_work_folders(meeting))},
        {"key": "registry", "label": "Записи в базе кабинета",
         "count": 1 + sum(counts[k] for k in ("tasks", "decisions", "people", "voices", "overlaps", "events")),
         "detail": f"задач {counts['tasks']}, решений {counts['decisions']}, участников {counts['people']}, отпечатков голосов {counts['voices']}"},
        {"key": "index", "label": "Фрагменты в поисковом индексе", "count": len(knowledge.sections(meeting))},
        {"key": "waveform", "label": "Волна для плеера", "count": 1 if (config.DATA_DIR / "waveforms" / f"{meeting_id}.json").exists() else 0},
        {"key": "noteCopies", "label": "Прежние копии заметки (после «Пересобрать по другому»)", "count": len(copies), "optional": True},
        {"key": "backups", "label": "Резервные копии базы, где есть эта запись", "count": len(in_backups), "optional": True,
         "detail": "из каждой копии убираются только строки этой записи, остальное в копии не меняется"},
    ]
    warnings = [
        "Копии вне приложения (Time Machine, iCloud, синхронизация Obsidian, git) приложение стереть не может.",
        "Файлы удаляются обычным способом, не перезаписываются поверх.",
    ]
    if counts["reminders"]:
        warnings.append(f"В «Напоминаниях» останутся напоминания по задачам этой записи: {counts['reminders']}.")
    if counts["voices"]:
        warnings.append("Если вы запоминали голос человека по этой записи, он остаётся в его профиле голоса "
                        "(профиль усреднён по нескольким записям). Удалить его можно в карточке человека.")
    return {"meetingId": meeting_id, "title": meeting.title, "date": meeting.date, "confirmWord": WORD, "items": items,
            "documents": [{"id": _doc_id(p), "name": p.name, "folder": p.parent.name} for p in documents],
            "warnings": warnings, "busy": _busy()}


def _busy() -> bool:
    from . import jobs
    try:
        return bool(jobs.is_running())
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- стирание

def _unlink(path: Path) -> int:
    try:
        path.unlink()
        return 1
    except FileNotFoundError:
        return 0


def _drop_empty(folder: Path, stop: Path | None = None) -> None:
    while folder.is_dir() and folder != stop:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def _scrub_registry(meeting_id: str, audio_names: set[str]) -> int:
    """Строки базы и «осколки» в настройках; стирание страниц базы — через secure_delete."""
    removed = 0
    with storage.connect() as db:
        db.execute("PRAGMA secure_delete = ON")
        decision_ids = [r[0] for r in db.execute("SELECT id FROM decisions WHERE meeting_id=?", (meeting_id,))]
        for table in ("tasks", "decisions", "meeting_people", "meeting_voices", "meeting_overlaps"):
            removed += db.execute(f"DELETE FROM {table} WHERE meeting_id=?", (meeting_id,)).rowcount
        removed += db.execute("DELETE FROM processing_events WHERE meeting_id=?", (meeting_id,)).rowcount
        removed += db.execute("DELETE FROM meetings WHERE id=?", (meeting_id,)).rowcount
    # пары «решение устарело / не связаны», где участвовало стёртое решение, и пометки файла в очереди
    from . import conflicts, jobs
    if decision_ids:
        pairs = conflicts._dismissed()
        kept = {p for p in pairs if not any(i in p.split("|") for i in decision_ids)}
        if kept != pairs:
            storage.set_setting(conflicts.SETTING, json.dumps(sorted(kept)))
    for name in audio_names:
        try:
            jobs.unignore_file(name)
            jobs.set_file_template(name, "")
        except Exception:  # noqa: BLE001 — настройки очереди вторичны
            pass
    try:
        with storage.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass
    return removed


def _scrub_backup(path: Path, meeting_id: str) -> bool:
    try:
        db = sqlite3.connect(path, timeout=15)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA secure_delete = ON")
            db.execute("PRAGMA journal_mode = DELETE")
            for table in ("tasks", "decisions", "meeting_people", "meeting_voices", "meeting_overlaps", "processing_events"):
                try:
                    db.execute(f"DELETE FROM {table} WHERE meeting_id=?", (meeting_id,))
                except sqlite3.Error:
                    continue
            db.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
            db.commit()
            db.execute("VACUUM")
        finally:
            db.close()
        return True
    except sqlite3.Error:
        return False


def _scrub_work_log(names: set[str]) -> None:
    """Журнал обработки хранит имена файлов и этапы; строки про стёртый файл убираем."""
    path = config.LOGS_DIR / "processing.jsonl"
    if not names or not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        kept = [line for line in lines if not any(name in line for name in names)]
        if len(kept) != len(lines):
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError:
        pass


def apply(core, meeting_id: str, confirm: str, *, scrub_backups: bool = True, note_copies: bool = True,
          documents: list[str] | None = None) -> dict:
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise EraseError("Запись не найдена", 404)
    if " ".join(str(confirm or "").split()).upper() != WORD:
        raise EraseError(f"Для подтверждения введите слово {WORD}")
    if _busy():
        raise EraseError("Идёт обработка файлов: дождитесь окончания и повторите", 409)
    audio, transcript = _audio_files(meeting), knowledge.transcript_file(meeting)
    names = {p.name for p in audio} | ({meeting.source_audio} if meeting.source_audio else set())
    chosen = set(documents or [])
    docs = [p for p in _documents(meeting) if _doc_id(p) in chosen]
    result = {"audio": 0, "attachments": 0, "transcript": 0, "work": 0, "waveform": 0, "registry": 0, "index": 0,
              "noteCopies": 0, "backups": 0, "documents": 0, "note": 0, "problems": []}

    def step(key: str, fn) -> None:
        try:
            result[key] += int(fn() or 0)
        except OSError as exc:
            result["problems"].append(f"{key}: {exc}")

    step("audio", lambda: sum(_unlink(p) for p in audio))
    step("attachments", lambda: attachments.erase_files(meeting))
    for folder in _work_folders(meeting):
        step("work", lambda f=folder: (shutil.rmtree(f), 1)[1])
    step("waveform", lambda: _unlink(config.DATA_DIR / "waveforms" / f"{meeting_id}.json"))
    if note_copies:
        step("noteCopies", lambda: sum(_unlink(p) for p in _note_copies(meeting)))
    step("documents", lambda: sum(_unlink(p) for p in docs))
    _scrub_work_log(names)
    # заметка — после всего остального; записи в базе стираются до заметки, чтобы при сбое повтор находил заметку
    if transcript:
        step("transcript", lambda: _unlink(transcript))
    if result["problems"]:
        # файл не удалился (занят, нет прав): запись в базе и заметку не трогаем, чтобы стирание можно было повторить
        result["interrupted"] = True
        return result
    try:
        result["registry"] = _scrub_registry(meeting_id, names)
    except sqlite3.Error as exc:
        result["problems"].append(f"registry: {exc}")
    core.index.delete_meeting(meeting_id)
    result["index"] = 1
    if scrub_backups:
        result["backups"] = sum(1 for p in _backups_with_record(meeting_id) if _scrub_backup(p, meeting_id))
    step("note", lambda: _unlink(meeting.path))
    for folder in (meeting.path.parent / "Транскрипты", meeting.path.parent):
        if folder != knowledge.VAULT:
            _drop_empty(folder, knowledge.VAULT)
    core.reload(force=True)
    try:
        from . import mirror
        mirror.refresh()
    except Exception:  # noqa: BLE001 — сводки пересоберутся при следующем изменении
        pass
    return result


def summary(result: dict) -> str:
    """Короткая подробность для журнала: только числа."""
    parts = [f"{k}: {result[k]}" for k in ("audio", "attachments", "registry", "backups", "documents") if result.get(k)]
    if result.get("problems"):
        parts.append(f"сбоев: {len(result['problems'])}")
    return ", ".join(parts)
