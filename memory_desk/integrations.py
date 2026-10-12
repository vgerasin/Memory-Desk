"""Связка с Календарём и Напоминаниями macOS: ближайшая встреча, привязка записей к событиям, задачи ↔ напоминания."""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import apple, calendar_source, config, knowledge, speakers, storage


# --------------------------------------------------------------------------- календарь

def apple_ready(cfg: dict, kind: str = "events") -> bool:
    if not cfg.get("apple_calendar" if kind == "events" else "reminders_enabled"):
        return False
    return apple.backend().status().get(kind) == "authorized"


def _attendee_names(event: dict) -> list[str]:
    names = [a["name"] for a in event.get("attendees", []) if not a.get("me") and a.get("name")]
    organizer = event.get("organizer")
    if organizer and organizer not in names and not any(a.get("me") and a.get("name") == organizer for a in event.get("attendees", [])):
        names.insert(0, organizer)
    return list(dict.fromkeys(" ".join(n.split()) for n in names))


def next_meeting(cfg: dict, now: datetime | None = None) -> dict | None:
    """Идущая или ближайшая сегодняшняя встреча в том же формате, что и у ICS. Apple Календарь важнее ICS."""
    now = now or datetime.now()
    if cfg.get("apple_calendar"):
        if apple_ready(cfg):
            try:
                day_start = datetime.combine(now.date(), datetime.min.time())
                found = []
                for ev in apple.backend().events(day_start, day_start + timedelta(days=1)):
                    start, end = datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"])
                    if ev["allDay"] or ev["canceled"] or end < now:
                        continue
                    found.append({"title": ev["title"], "start": start.isoformat(timespec="minutes"),
                                  "end": end.isoformat(timespec="minutes"), "people": _attendee_names(ev)[:8],
                                  "location": ev["location"], "ongoing": start <= now <= end})
                if found:
                    return found[0]
                return None
            except apple.AppleError:
                pass
    return calendar_source.upcoming(cfg.get("calendar_source", ""), now)


MAX_RANGE_DAYS = 366


def calendar_range(core, first: date, last: date) -> dict:
    """События за произвольный диапазон дат (Календарь macOS или ICS) и записи, которые к ним относятся.
    Возвращает дни с событиями и записями без события; ошибка источника не превращается в 500."""
    if last < first:
        first, last = last, first
    if (last - first).days >= MAX_RANGE_DAYS:
        raise ValueError(f"Диапазон слишком большой: не больше {MAX_RANGE_DAYS} дней")
    cfg = core.cfg
    start, stop = datetime.combine(first, datetime.min.time()), datetime.combine(last + timedelta(days=1), datetime.min.time())
    source, error, events = "none", "", []
    if cfg.get("apple_calendar") and apple_ready(cfg):
        try:
            for ev in apple.backend().events(start, stop):
                if ev["canceled"]:
                    continue
                events.append({"id": ev["id"], "title": ev["title"], "start": ev["start"][:16], "end": ev["end"][:16], "allDay": ev["allDay"],
                               "calendar": ev["calendar"], "location": ev["location"], "people": _attendee_names(ev)[:12]})
            source = "apple"
        except apple.AppleError as exc:
            error = str(exc)
    if source == "none" and cfg.get("calendar_source", "").strip():
        try:
            events = calendar_source.between(cfg["calendar_source"], start, stop)
            source = "ics"
        except Exception as exc:  # noqa: BLE001 — недоступный файл или ссылка не должны ломать экран
            error = f"Не удалось прочитать календарь: {exc}"
    elif source == "none" and cfg.get("apple_calendar") and not error:
        error = "Нет доступа к Календарю macOS: разрешите его в настройках"

    linked = {r["calendar_event_id"]: r["id"] for r in storage.rows("SELECT id, calendar_event_id FROM meetings WHERE calendar_event_id!='' AND calendar_event_id IS NOT NULL")}
    records = []
    for m in core.live_meetings():
        spans = _intervals(m)
        if spans and start - timedelta(hours=3) <= spans[0][0] <= stop + timedelta(hours=3):
            records.append((m, spans))
    by_id = {m.id: m for m, _ in records}
    used: set[str] = set()
    for ev in events:
        span = (datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"]))
        matched = []
        for m, spans in records:
            if linked.get(ev["id"]) == m.id:
                matched.append(m)
                continue
            if ev["allDay"] or m.id in linked.values():
                continue
            length = max(60.0, (span[1] - span[0]).total_seconds())
            for interval in spans:
                shortest = max(60.0, min(length, (interval[1] - interval[0]).total_seconds() or length))
                near = abs((interval[0] - span[0]).total_seconds()) <= 15 * 60
                if _overlap(interval, span) / shortest >= 0.3 or near:
                    matched.append(m)
                    break
        ev["records"] = [{"id": m.id, "title": m.title, "linked": linked.get(ev["id"]) == m.id} for m in matched]
        used.update(m.id for m in matched)

    days: dict[str, dict] = {}
    cursor = first
    while cursor <= last:
        days[cursor.isoformat()] = {"date": cursor.isoformat(), "events": [], "records": []}
        cursor += timedelta(days=1)
    for ev in events:
        day = max(ev["start"][:10], first.isoformat())  # событие, начавшееся до диапазона, показываем в его первый день
        if day in days:
            days[day]["events"].append(ev)
    for m, _spans in records:
        day = (m.recorded_at or "")[:10] or m.date
        if m.id not in used and day in days:
            days[day]["records"].append({"id": m.id, "title": m.title, "time": m.time})
    shown = [d for d in days.values() if d["events"] or d["records"]]
    return {"from": first.isoformat(), "to": last.isoformat(), "source": source, "error": error, "days": shown,
            "totals": {"events": len(events), "withRecord": sum(1 for e in events if e["records"]), "recordsWithoutEvent": sum(len(d["records"]) for d in shown)},
            "configured": bool(cfg.get("apple_calendar") or cfg.get("calendar_source", "").strip())}


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _intervals(m: knowledge.Meeting) -> list[tuple[datetime, datetime]]:
    start = _parse_dt(m.recorded_at)
    if not start:
        return []
    duration = timedelta(seconds=knowledge.to_seconds(m.duration) or 0)
    # В имени файла PLAUD может быть и начало, и конец записи, поэтому проверяем оба варианта.
    return [(start, start + duration), (start - duration, start)]


def _overlap(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> float:
    return max(0.0, (min(a[1], b[1]) - max(a[0], b[0])).total_seconds())


def find_event(m: knowledge.Meeting) -> dict | None:
    intervals = _intervals(m)
    if not intervals:
        return None
    lo = min(i[0] for i in intervals) - timedelta(hours=2)
    hi = max(i[1] for i in intervals) + timedelta(hours=2)
    best, best_key = None, None
    for ev in apple.backend().events(lo, hi):
        if ev["allDay"] or ev["canceled"]:
            continue
        span = (datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"]))
        length = max(60.0, (span[1] - span[0]).total_seconds())
        for interval in intervals:
            overlap = _overlap(interval, span)
            shortest = max(60.0, min(length, (interval[1] - interval[0]).total_seconds() or length))
            near = abs((interval[0] - span[0]).total_seconds()) <= 15 * 60
            if overlap / shortest < 0.3 and not near:
                continue
            key = (overlap, -abs((interval[0] - span[0]).total_seconds()))
            if best_key is None or key > best_key:
                best, best_key = ev, key
    return best


def _suggested_project(core, event_title: str, exclude_id: str) -> dict | None:
    folded = event_title.casefold()
    row = storage.one("SELECT project_id, project_title FROM meetings WHERE id!=? AND project_id!='' AND project_id IS NOT NULL "
                      "AND lower(calendar_event_title)=? ORDER BY recorded_at DESC LIMIT 1", (exclude_id, folded))
    if row:
        return {"id": row["project_id"], "title": row["project_title"]}
    for project in core.projects():
        if project["title"].casefold() in folded:
            return {"id": project["id"], "title": project["title"]}
    return None


def calendar_suggestion(core, m: knowledge.Meeting) -> dict | None:
    cfg = core.cfg
    if not apple_ready(cfg):
        return None
    row = storage.one("SELECT calendar_event_id, calendar_event_title, calendar_dismissed FROM meetings WHERE id=?", (m.id,)) or {}
    if row.get("calendar_event_id"):
        return {"linked": True, "title": row.get("calendar_event_title") or ""}
    if row.get("calendar_dismissed"):
        return None
    try:
        event = find_event(m)
    except apple.AppleError:
        return None
    if not event:
        return None
    people = [speakers.canonical_name(n) for n in _attendee_names(event)]
    project = None if m.project_id else _suggested_project(core, event["title"], m.id)
    return {"linked": False, "event": {"id": event["id"], "title": event["title"], "start": event["start"], "end": event["end"],
                                       "calendar": event["calendar"]},
            "people": list(dict.fromkeys(people)), "project": project, "titleDiffers": event["title"].strip() != m.title.strip()}


def apply_suggestion(core, m: knowledge.Meeting, *, use_title: bool = False, use_project: bool = True) -> dict:
    suggestion = calendar_suggestion(core, m)
    if not suggestion or suggestion.get("linked"):
        raise ValueError("Подходящего события нет")
    event = suggestion["event"]
    if suggestion["people"]:
        storage.add_meeting_people(m.id, suggestion["people"])
    if use_project and suggestion["project"]:
        project = next((p for p in core.projects() if p["id"] == suggestion["project"]["id"]), None)
        if project:
            knowledge.assign_project(m, project)
    if use_title and suggestion["titleDiffers"]:
        knowledge.update_meeting_text(m, event["title"], "")
    storage.update_item("meetings", m.id, calendar_event_id=event["id"], calendar_event_title=event["title"])
    core.reload(force=True)
    return suggestion


def dismiss_suggestion(meeting_id: str) -> None:
    storage.update_item("meetings", meeting_id, calendar_dismissed=1)


def create_event(cfg: dict, *, title: str, start: datetime, end: datetime | None, all_day: bool, notes: str, url: str) -> str:
    be = apple.backend()
    if be.status().get("events") != "authorized":
        raise apple.AppleError("Нет доступа к Календарю: разрешите его в настройках")
    if all_day:
        day = datetime.combine(start.date(), datetime.min.time())
        start, end = day, day + timedelta(days=1) - timedelta(seconds=1)
    else:
        end = end or start + timedelta(hours=1)
        if end <= start:
            raise ValueError("Окончание должно быть позже начала")
    return be.create_event(title, start, end, all_day=all_day, notes=notes, url=url)


# --------------------------------------------------------------------------- напоминания

_sync_lock = threading.Lock()
last_sync: dict = {"at": None, "summary": None, "error": ""}


def _is_mine(cfg: dict, owner: str) -> bool:
    folded = (owner or "").strip().casefold()
    if folded in {"", "не указан", "не указано", "вы", "я"}:
        return True
    mine = (cfg.get("my_name") or "").strip().casefold()
    return bool(mine) and (folded == mine or folded == mine.split()[0] or set(folded.split()) == set(mine.split()))


def _reminder_title(cfg: dict, task: dict) -> str:
    owner = (task.get("owner") or "").strip()
    return task["text"] if _is_mine(cfg, owner) else f"{task['text']} ({owner})"


def _reminder_notes(task: dict) -> str:
    lines = [f"Из записи «{task['mt']}» · {task['md']}"]
    if (task.get("owner") or "").strip():
        lines.append(f"Ответственный: {task['owner']}")
    return "\n".join(lines)


def _reminder_url(task: dict) -> str:
    sec = knowledge.to_seconds(task.get("evidence_time") or "")
    return f"http://127.0.0.1:{config.PORT}/records/{task['meeting_id']}" + (f"?t={sec}" if sec else "")


def _fingerprint(cfg: dict, task: dict) -> str:
    raw = f"{_reminder_title(cfg, task)}|{task.get('deadline') or ''}|{task.get('owner') or ''}|{task['mt']}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _due(task: dict) -> date | None:
    return apple.to_date(task.get("deadline") or "") if (task.get("deadline") or "")[:4].isdigit() else None


def _note_follows(task: dict, status: str) -> None:
    """Галочка в заметке следует за статусом задачи (в том числе когда задачу закрыли в Напоминаниях)."""
    row = storage.one("SELECT path FROM meetings WHERE id=?", (task["meeting_id"],))
    if not row:
        return
    try:
        knowledge.update_item_line(Path(row["path"]), "tasks", task, {**task, "status": status})
    except OSError:
        pass


def sync_reminders(cfg: dict) -> dict:
    """Один проход синхронизации. Возвращает счётчики; ошибки не прерывают обработку остальных задач."""
    summary = {"created": 0, "updated": 0, "completedInApp": 0, "completedInReminders": 0, "removed": 0, "errors": 0}
    be = apple.backend()
    if be.status().get("reminders") != "authorized":
        raise apple.AppleError("Нет доступа к Напоминаниям: разрешите его в настройках")
    be.refresh()
    list_title = cfg.get("reminders_list") or "Memory Desk"
    scope_all = cfg.get("reminders_scope") == "all"
    tasks = storage.rows("SELECT t.*, m.title AS mt, m.meeting_date AS md, m.archived AS archived FROM tasks t "
                         "JOIN meetings m ON m.id=t.meeting_id")
    links = {r["task_id"]: r for r in storage.rows("SELECT * FROM reminder_links")}
    now = storage.now_iso()
    first_error = ""
    for task in tasks:
        link = links.get(task["id"])
        try:
            if link is None:
                if task["status"] != "open" or task["archived"] or not (scope_all or _is_mine(cfg, task.get("owner") or "")):
                    continue
                rid = be.create_reminder(list_title, _reminder_title(cfg, task), _due(task), _reminder_notes(task), _reminder_url(task))
                with storage.connect() as db:
                    db.execute("INSERT OR REPLACE INTO reminder_links (task_id,reminder_id,list_title,fingerprint,task_done,"
                               "reminder_done,state,updated_at) VALUES (?,?,?,?,0,0,'active',?)",
                               (task["id"], rid, list_title, _fingerprint(cfg, task), now))
                summary["created"] += 1
                continue
            if link["state"] != "active":
                continue
            if task["status"] == "rejected":
                state = be.reminder_state(link["reminder_id"])
                if state and not state["completed"]:
                    be.delete_reminder(link["reminder_id"])
                    summary["removed"] += 1
                _set_link(task["id"], state="removed")
                continue
            state = be.reminder_state(link["reminder_id"])
            if state is None:  # напоминание удалили вручную: заново не создаём
                _set_link(task["id"], state="removed")
                continue
            task_done = task["status"] == "done"
            if state["completed"] != bool(link["reminder_done"]):  # изменено в Напоминаниях — оно главное
                if task["status"] in ("open", "done"):
                    new_status = "done" if state["completed"] else "open"
                    storage.update_item("tasks", task["id"], status=new_status)
                    _note_follows(task, new_status)
                    summary["completedInReminders"] += 1
                _set_link(task["id"], task_done=int(state["completed"]), reminder_done=int(state["completed"]))
            elif task_done != bool(link["task_done"]):  # изменено в приложении
                be.update_reminder(link["reminder_id"], completed=task_done)
                summary["completedInApp"] += 1
                _set_link(task["id"], task_done=int(task_done), reminder_done=int(task_done))
            fp = _fingerprint(cfg, task)
            if fp != link["fingerprint"] and not task_done:
                be.update_reminder(link["reminder_id"], title=_reminder_title(cfg, task), due=_due(task),
                                   notes=_reminder_notes(task))
                _set_link(task["id"], fingerprint=fp)
                summary["updated"] += 1
        except apple.AppleError as exc:
            summary["errors"] += 1
            first_error = first_error or str(exc)
    if first_error:
        summary["firstError"] = first_error
    return summary


def _set_link(task_id: str, **fields) -> None:
    fields["updated_at"] = storage.now_iso()
    with storage.connect() as db:
        db.execute(f"UPDATE reminder_links SET {','.join(f'{k}=?' for k in fields)} WHERE task_id=?", (*fields.values(), task_id))


def run_sync(cfg: dict) -> dict:
    """Одиночный запуск с записью результата для окна настроек."""
    if not _sync_lock.acquire(blocking=False):
        return {"skipped": True}
    try:
        storage.initialize()
        try:
            summary = sync_reminders(cfg)
            last_sync.update(at=time.time(), summary=summary, error=summary.get("firstError", ""))
            return summary
        except apple.AppleError as exc:
            last_sync.update(at=time.time(), summary=None, error=str(exc))
            raise
    finally:
        _sync_lock.release()


def schedule_sync(cfg: dict) -> None:
    """Быстрая синхронизация в фоне после изменения задачи."""
    if cfg.get("reminders_enabled"):
        threading.Thread(target=lambda: _safe(cfg), name="reminders-sync", daemon=True).start()


def _safe(cfg: dict) -> None:
    try:
        run_sync(cfg)
    except Exception:  # noqa: BLE001 — результат уже записан в last_sync
        pass


def status_payload(cfg: dict) -> dict:
    be = apple.backend().status()
    return {**be, "calendarEnabled": bool(cfg.get("apple_calendar")), "remindersEnabled": bool(cfg.get("reminders_enabled")),
            "lastSync": dict(last_sync)}
