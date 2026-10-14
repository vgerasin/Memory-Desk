"""Журнал действий: что менялось в кабинете, когда, кем и с каким результатом.

Файл `audit.log` в папке данных, по JSON-строке на действие, только добавление. Каждая строка несёт хэш
предыдущей, поэтому правка или удаление строки задним числом видны при проверке цепочки (`verify`). Это не защита
от человека с доступом к файлам, а способ заметить случайную порчу и подмену.

Содержимого записей в журнале нет: только идентификаторы (запись, задача, проект, человек), коды действий и
результат. Названия записей подставляются при показе, из текущей базы; стёртая запись показывается как «стёртая
запись», и в журнале от её названия ничего не остаётся.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from . import config

ACTORS = {"cabinet": "Кабинет", "system": "Система", "cli": "Командная строка", "mcp": "MCP"}
RESULTS = {"ok": "выполнено", "error": "ошибка", "denied": "отказано"}
GENESIS = "0" * 32
_lock = threading.Lock()
_tail: dict[str, str] = {}      # путь файла -> хэш последней строки (чтобы не читать файл на каждую запись)


def path() -> Path:
    return config.DATA_DIR / "audit.log"


# --------------------------------------------------------------------------- каталог действий

# (метод, маршрут) -> (код, название, тип объекта, параметр маршрута с идентификатором объекта)
ACTIONS: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("POST", "/api/system/backup"): ("backup.create", "Резервная копия базы", "system", ""),
    ("POST", "/api/system/mirror"): ("mirror.refresh", "Обновление сводок в Obsidian", "system", ""),
    ("POST", "/api/system/restore"): ("backup.restore", "Восстановление базы из копии", "system", ""),
    ("POST", "/api/system/reindex"): ("index.rebuild", "Перестройка поискового индекса", "system", ""),
    ("POST", "/api/integrations/apple/access"): ("apple.access", "Запрос доступа к Календарю и Напоминаниям", "system", ""),
    ("POST", "/api/integrations/reminders/sync"): ("reminders.sync", "Синхронизация с «Напоминаниями»", "system", ""),
    ("POST", "/api/calendar/events"): ("calendar.event", "Событие в Календаре", "system", ""),
    ("POST", "/api/storage/clean"): ("storage.clean", "Очистка хранилища", "system", ""),
    ("PUT", "/api/settings"): ("settings.update", "Изменение настроек", "system", ""),
    ("PUT", "/api/dictionary"): ("dictionary.update", "Изменение словаря терминов", "system", ""),
    ("POST", "/api/dictionary/apply"): ("dictionary.apply", "Применение словаря к готовым записям", "system", ""),
    ("POST", "/api/dictionary/suggestions/accept"): ("dictionary.accept", "Слово из правок добавлено в словарь", "system", ""),
    ("POST", "/api/dictionary/suggestions/dismiss"): ("dictionary.dismiss", "Предложение для словаря отклонено", "system", ""),
    ("PUT", "/api/templates/{template_id}"): ("template.save", "Сохранение шаблона заметки", "template", "template_id"),
    ("DELETE", "/api/templates/{template_id}"): ("template.delete", "Удаление шаблона заметки", "template", "template_id"),
    ("POST", "/api/jobs/template"): ("job.template", "Выбор шаблона для файла", "system", ""),
    ("POST", "/api/meetings/{meeting_id}/rebuild"): ("record.rebuild", "Пересборка заметки по шаблону", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/bookmarks"): ("bookmark.add", "Закладка на записи", "meeting", "meeting_id"),
    ("PATCH", "/api/meetings/{meeting_id}/bookmarks/{sec}"): ("bookmark.update", "Правка закладки", "meeting", "meeting_id"),
    ("DELETE", "/api/meetings/{meeting_id}/bookmarks/{sec}"): ("bookmark.delete", "Удаление закладки", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/attachments"): ("attachment.add", "Вложение к записи", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/attachments/text"): ("attachment.add", "Текстовая заметка к записи", "meeting", "meeting_id"),
    ("PATCH", "/api/meetings/{meeting_id}/attachments/{att_id}"): ("attachment.update", "Правка вложения", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/attachments/{att_id}/recognize"): ("attachment.recognize", "Повторное распознавание снимка", "meeting", "meeting_id"),
    ("DELETE", "/api/meetings/{meeting_id}/attachments/{att_id}"): ("attachment.delete", "Удаление вложения", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/views"): ("view.add", "Взгляд на запись", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/views/{view_id}/refresh"): ("view.refresh", "Пересчёт взгляда", "meeting", "meeting_id"),
    ("DELETE", "/api/meetings/{meeting_id}/views/{view_id}"): ("view.delete", "Удаление взгляда", "meeting", "meeting_id"),
    ("PATCH", "/api/meetings/{meeting_id}"): ("record.update", "Правка записи", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/publish"): ("record.publish", "Публикация записи", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/archive"): ("record.archive", "Запись в архив", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/restore"): ("record.restore", "Возврат записи из архива", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/erase"): ("record.erase", "Запись стёрта целиком", "meeting", "meeting_id"),
    ("POST", "/api/materials/save"): ("document.save", "Документ сохранён в Obsidian", "system", ""),
    ("POST", "/api/meetings/{meeting_id}/calendar"): ("record.calendar", "Связь записи с событием календаря", "meeting", "meeting_id"),
    ("PUT", "/api/meetings/{meeting_id}/speakers"): ("record.speakers", "Имена говорящих", "meeting", "meeting_id"),
    ("POST", "/api/meetings/{meeting_id}/diarize"): ("record.diarize", "Разметка говорящих", "meeting", "meeting_id"),
    ("POST", "/api/projects"): ("project.create", "Новый проект", "project", ""),
    ("PATCH", "/api/projects/{project_id}"): ("project.update", "Правка проекта", "project", "project_id"),
    ("POST", "/api/decisions/conflicts/dismiss"): ("decision.dismiss", "Пара решений признана несвязанной", "system", ""),
    ("POST", "/api/people"): ("person.save", "Карточка человека", "person", ""),
    ("PATCH", "/api/people/{person_id}"): ("person.update", "Правка карточки человека", "person", "person_id"),
    ("POST", "/api/voice/model"): ("voice.model", "Загрузка модели голосов", "system", ""),
    ("POST", "/api/people/{person_id}/voice"): ("voice.remember", "Голос человека запомнен", "person", "person_id"),
    ("DELETE", "/api/people/{person_id}/voice"): ("voice.forget", "Голос человека забыт", "person", "person_id"),
    ("PATCH", "/api/tasks/{item_id}"): ("task.update", "Правка задачи", "task", "item_id"),
    ("PATCH", "/api/decisions/{item_id}"): ("decision.update", "Правка решения", "decision", "item_id"),
    ("POST", "/api/tasks/bulk"): ("task.bulk", "Массовое действие с задачами", "task", ""),
    ("POST", "/api/decisions/bulk"): ("decision.bulk", "Массовое действие с решениями", "decision", ""),
    ("POST", "/api/meetings/bulk"): ("record.bulk", "Массовое действие с записями", "meeting", ""),
    ("POST", "/api/jobs/scan"): ("job.scan", "Обработка новых файлов", "system", ""),
    ("POST", "/api/jobs/cancel"): ("job.cancel", "Остановка обработки", "system", ""),
    ("POST", "/api/jobs/retry"): ("job.retry", "Повтор обработки файла", "system", ""),
    ("POST", "/api/jobs/dismiss"): ("job.dismiss", "Файл убран из очереди", "system", ""),
    ("POST", "/api/chat/save"): ("document.save", "Ответ чата сохранён в Obsidian", "system", ""),
    ("PATCH", "/api/suggestions/{sid}"): ("suggestion.update", "Правка предложения", "suggestion", "sid"),
    ("POST", "/api/suggestions/{sid}/create"): ("suggestion.create", "Предложение выполнено", "suggestion", "sid"),
    ("POST", "/api/suggestions/{sid}/dismiss"): ("suggestion.dismiss", "Предложение отклонено", "suggestion", "sid"),
    ("POST", "/api/suggestions/{sid}/restore"): ("suggestion.restore", "Предложение возвращено", "suggestion", "sid"),
    ("POST", "/api/people/{person_id}/pins"): ("dossier.pin", "Факт закреплён в досье человека", "person", "person_id"),
    ("POST", "/api/projects/{project_id}/pins"): ("dossier.pin", "Факт закреплён в досье проекта", "project", "project_id"),
    ("PATCH", "/api/pins/{pin_id}"): ("dossier.pin_edit", "Закреплённый факт изменён", "pin", "pin_id"),
    ("DELETE", "/api/pins/{pin_id}"): ("dossier.unpin", "Закреплённый факт убран", "pin", "pin_id"),
    ("POST", "/api/people/{person_id}/hanging/mute"): ("dossier.mute", "Вопрос в досье человека закрыт вручную", "person", "person_id"),
    ("POST", "/api/projects/{project_id}/hanging/mute"): ("dossier.mute", "Вопрос в досье проекта закрыт вручную", "project", "project_id"),
    ("DELETE", "/api/hanging/{mute_id}"): ("dossier.unmute", "Вопрос возвращён в висящие", "system", ""),
    ("POST", "/api/people/{person_id}/refresh"): ("dossier.refresh_request", "Обновление досье человека", "person", "person_id"),
    ("POST", "/api/projects/{project_id}/refresh"): ("dossier.refresh_request", "Обновление досье проекта", "project", "project_id"),
    ("POST", "/api/lock/pin"): ("lock.pin", "PIN: установка или смена", "system", ""),
    ("POST", "/api/lock/remove"): ("lock.pin_remove", "PIN снят", "system", ""),
    ("PUT", "/api/lock/timeout"): ("lock.timeout_set", "Изменено время блокировки", "system", ""),
    ("POST", "/api/lock/unlock"): ("lock.unlock", "Вход по PIN", "system", ""),
    ("POST", "/api/lock/lock"): ("lock.lock", "Блокировка", "system", ""),
}

# Запросы, которые данные не меняют: строят текст, ответ или файл для скачивания.
READ_ONLY: set[tuple[str, str]] = {
    ("POST", "/api/meetings/{meeting_id}/protocol"), ("POST", "/api/meetings/{meeting_id}/protocol/start"),
    ("POST", "/api/materials/mindmap"), ("POST", "/api/materials/artifact"), ("POST", "/api/materials/digest"),
    ("POST", "/api/decisions/conflicts/check"),
    ("POST", "/api/templates/draft"), ("POST", "/api/chat"), ("POST", "/api/chat/export"),
    ("POST", "/api/export"), ("POST", "/api/export/map"), ("POST", "/api/lock/ping"),
    ("POST", "/api/meetings/{meeting_id}/suggestions/scan"),   # запись в журнал делает сам поиск (suggestion.scan)
}


def result_of(status: int) -> str:
    if status in (401, 403, 423, 429):
        return "denied"
    return "ok" if status < 400 else "error"


# --------------------------------------------------------------------------- запись

def _digest(prev: str, body: str) -> str:
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()[:32]


def _last_hash(file: Path) -> str:
    key = str(file)
    if key in _tail:
        return _tail[key]
    last = GENESIS
    if file.exists():
        with file.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4096))
            lines = handle.read().splitlines()
        for raw in reversed(lines):
            try:
                last = json.loads(raw.decode("utf-8"))["h"]
                break
            except (ValueError, KeyError, UnicodeDecodeError):
                continue
    _tail[key] = last
    return last


def record(actor: str, action: str, *, target: str = "", target_id: str = "", result: str = "ok", detail: str = "") -> dict:
    """Добавляет строку в журнал. Сбой записи журнала не должен ломать само действие, поэтому исключения гасятся."""
    entry = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "actor": actor if actor in ACTORS else "cabinet",
             "action": action, "target": target, "id": target_id, "result": result if result in RESULTS else "error",
             "detail": " ".join(str(detail or "").split())[:200]}
    file = path()
    try:
        with _lock:
            file.parent.mkdir(parents=True, exist_ok=True)
            prev = _last_hash(file)
            body = json.dumps({**entry, "prev": prev}, ensure_ascii=False, sort_keys=True)
            line = json.dumps({**json.loads(body), "h": _digest(prev, body)}, ensure_ascii=False, sort_keys=True)
            fd = os.open(file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            _tail[str(file)] = json.loads(line)["h"]
    except OSError:
        _tail.pop(str(file), None)
    return entry


def detail(request, text: str) -> None:
    """Обработчик дописывает к строке журнала короткую подробность (без содержимого записей)."""
    request.state.audit_detail = text


# --------------------------------------------------------------------------- чтение

def read_all() -> list[dict]:
    file = path()
    rows: list[dict] = []
    if not file.exists():
        return rows
    with file.open(encoding="utf-8", errors="replace") as handle:
        for number, raw in enumerate(handle, 1):
            try:
                row = json.loads(raw)
            except ValueError:
                row = {"action": "?", "actor": "", "result": "error", "ts": "", "damaged": True}
            row["n"] = number
            rows.append(row)
    return rows


def verify() -> dict:
    """Проверка цепочки: каждая строка должна ссылаться на хэш предыдущей и совпадать со своим хэшем."""
    file = path()
    if not file.exists():
        return {"ok": True, "count": 0, "badLine": None, "message": "журнал ещё пуст"}
    prev, count = GENESIS, 0
    with file.open(encoding="utf-8", errors="replace") as handle:
        for number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            count += 1
            try:
                row = json.loads(raw)
                stored = row.pop("h")
                body = json.dumps(row, ensure_ascii=False, sort_keys=True)
            except (ValueError, KeyError):
                return {"ok": False, "count": count, "badLine": number, "message": f"строка {number} повреждена"}
            if row.get("prev") != prev or _digest(prev, body) != stored:
                return {"ok": False, "count": count, "badLine": number, "message": f"цепочка нарушена на строке {number}"}
            prev = stored
    return {"ok": True, "count": count, "badLine": None, "message": "цепочка цела"}


def _matches(row: dict, actor: str, result: str, kind: str, q: str, since: str, until: str, labels: dict[str, str]) -> bool:
    if actor and row.get("actor") != actor:
        return False
    if result and row.get("result") != result:
        return False
    if kind and not str(row.get("action", "")).startswith(kind):
        return False
    stamp = str(row.get("ts", ""))[:10]
    if since and stamp < since:
        return False
    if until and stamp > until:
        return False
    if q:
        haystack = " ".join(str(row.get(k, "")) for k in ("action", "id", "detail", "target")) + " " + labels.get(row.get("action", ""), "")
        if q.casefold() not in haystack.casefold():
            return False
    return True


def labels() -> dict[str, str]:
    result = {code: label for code, label, _t, _p in ACTIONS.values()}
    result.update({"record.processed": "Запись обработана", "backup.auto": "Автоматическая копия базы",
                   "lock.blocked": "Запрос отклонён: приложение заблокировано",
                   "suggestion.scan": "Поиск договорённостей в записи", "lock.timeout": "Блокировка по бездействию", "lock.reset": "PIN сброшен с компьютера",
                   "dossier.refresh": "Досье пересчитано"})
    return result


def query(*, actor: str = "", result: str = "", kind: str = "", q: str = "", since: str = "", until: str = "",
          limit: int = 100, offset: int = 0) -> dict:
    names = labels()
    rows = [r for r in read_all() if _matches(r, actor, result, kind, q, since, until, names)]
    rows.reverse()      # новые сверху
    total = len(rows)
    page = rows[offset: offset + max(1, limit)]
    return {"total": total, "items": page, "labels": names}


def to_csv(rows: list[dict], titles: dict[str, str] | None = None) -> str:
    names = labels()
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Время", "Кто", "Действие", "Код", "Объект", "Идентификатор", "Подробности", "Результат"])
    for row in rows:
        ident = str(row.get("id", ""))
        writer.writerow([row.get("ts", ""), ACTORS.get(row.get("actor", ""), row.get("actor", "")), names.get(row.get("action", ""), row.get("action", "")),
                         row.get("action", ""), (titles or {}).get(ident, "") or row.get("target", ""), ident, row.get("detail", ""),
                         RESULTS.get(row.get("result", ""), row.get("result", ""))])
    return "﻿" + out.getvalue()      # BOM, чтобы Excel открыл кириллицу
