"""Ежедневная польза: зависшие задачи и утренняя сводка («Сегодня» и одно уведомление в день)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import apple, config, integrations, storage

STUCK_DAYS = 14          # задача в работе без движения дольше — «зависла»
UPCOMING_DAYS = 7        # окно «на этой неделе»
NO_DEADLINE = {"", "не указан", "не указано"}


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def stuck_fields(row: dict, meeting_date: str | None, today: date | None = None) -> dict:
    """Возраст и признаки «зависшей» задачи. Движение — последняя правка или дата самой записи, смотря что новее.
    Зависшей считается только задача в работе (статус open): черновики ещё ждут проверки, а не исполнения."""
    today = today or date.today()
    last = max((d for d in (_day(row.get("updated_at")), _day(meeting_date)) if d), default=None)
    age = (today - last).days if last else 0
    no_deadline = (row.get("deadline") or "").strip().casefold() in NO_DEADLINE
    stuck = row.get("status") == "open" and age >= STUCK_DAYS
    reasons = []
    if stuck:
        reasons.append(f"без движения {age} дн.")
        if no_deadline:
            reasons.append("без срока")
    return {"ageDays": max(age, 0), "stuck": stuck, "stuckReasons": reasons}


def _is_mine(cfg: dict, owner: str) -> bool:
    return integrations._is_mine(cfg, owner) and bool((owner or "").strip().casefold() not in {"", "не указан", "не указано"})


def _owner_unknown(owner: str) -> bool:
    return (owner or "").strip().casefold() in {"", "не указан", "не указано"}


def _events_today(core, today: date) -> list[dict]:
    try:
        result = integrations.calendar_range(core, today, today)
    except (ValueError, apple.AppleError, OSError):
        return []
    now = datetime.now()
    events = []
    for day in result["days"]:
        for ev in day["events"]:
            if ev.get("allDay"):
                continue
            end = datetime.fromisoformat(ev["end"]) if ev.get("end") else None
            if end and end < now and today == now.date():
                continue
            events.append(ev)
    return sorted(events, key=lambda e: e["start"])


def morning_payload(core, today: date | None = None) -> dict:
    """Что важно сегодня: просрочено, обещано вами и вам на этой неделе, встречи с «главным к встрече», зависшие задачи."""
    from . import services
    today = today or date.today()
    cfg = core.cfg
    meta = {r["id"]: r for r in storage.rows("SELECT id,title,meeting_date FROM meetings WHERE archived=0")}
    rows = [r for r in storage.rows("SELECT t.* FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status='open' AND m.archived=0")
            if r["meeting_id"] in meta]
    tasks = [{**services._task_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"]),
              **stuck_fields(r, meta[r["meeting_id"]]["meeting_date"], today)} for r in rows]
    today_iso, week_iso = today.isoformat(), (today + timedelta(days=UPCOMING_DAYS)).isoformat()
    overdue = sorted((t for t in tasks if t["due"] and t["due"] < today_iso), key=lambda t: t["due"])
    week = [t for t in tasks if t["due"] and today_iso <= t["due"] <= week_iso]
    week.sort(key=lambda t: t["due"])
    mine = [t for t in week if _is_mine(cfg, t["owner"])]
    promised = [t for t in week if not _is_mine(cfg, t["owner"]) and not _owner_unknown(t["owner"])]
    stuck = [t for t in tasks if t["stuck"]]

    projects = core.projects()
    meetings = []
    for ev in _events_today(core, today):
        title_cf = ev["title"].casefold()
        project = next((p for p in projects if p["title"].casefold() in title_cf), None)
        headline = ""
        if project:
            try:
                headline = services.prep_brief(core, project_id=project["id"])["headline"]["text"]
            except KeyError:
                headline = ""
        meetings.append({"title": ev["title"], "start": ev["start"], "end": ev["end"], "people": ev.get("people", []),
                         "project": {"id": project["id"], "title": project["title"]} if project else None,
                         "headline": headline, "hasRecord": bool(ev.get("records"))})

    def cut(items: list[dict], limit: int = 5) -> list[dict]:
        return [{k: t[k] for k in ("id", "text", "owner", "due", "dueText", "meetingId", "meetingTitle", "tSec", "ageDays")} for t in items[:limit]]

    counts = {"overdue": len(overdue), "mine": len(mine), "promised": len(promised), "meetings": len(meetings), "stuck": len(stuck)}
    return {"date": today_iso, "counts": counts, "overdue": cut(overdue), "mine": cut(mine), "promised": cut(promised),
            "meetings": meetings, "stuck": cut(sorted(stuck, key=lambda t: -t["ageDays"]), 3),
            "empty": not any(counts.values()), "message": brief_message(counts)}


def _plural(n: int, one: str, few: str, many: str) -> str:
    word = one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many
    return f"{n} {word}"


def brief_message(counts: dict) -> str:
    """Одна строка для уведомления; пустая, если сказать нечего."""
    parts = []
    if counts.get("overdue"):
        n = counts["overdue"]
        verb = "просрочена" if n % 10 == 1 and n % 100 != 11 else "просрочено"
        parts.append(f"{verb} {_plural(n, 'задача', 'задачи', 'задач')}")
    if counts.get("meetings"):
        parts.append(f"сегодня {_plural(counts['meetings'], 'встреча', 'встречи', 'встреч')}")
    if counts.get("mine"):
        parts.append(f"ваших на неделе: {counts['mine']}")
    if counts.get("promised"):
        parts.append(f"вам обещали на неделе: {counts['promised']}")
    text = ", ".join(parts)
    return (text[0].upper() + text[1:]) if text else ""


# --------------------------------------------------------------------------- одно уведомление в день

def _state_path():
    return config.DATA_DIR / "morning.json"


def last_sent() -> str:
    try:
        return str(json.loads(_state_path().read_text(encoding="utf-8")).get("date", ""))
    except (OSError, ValueError):
        return ""


def mark_sent(day: str) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps({"date": day}), encoding="utf-8")
    temp.replace(path)


def send_morning_notification(core, now: datetime | None = None) -> bool:
    """Одно уведомление в сутки после заданного часа. Если сказать нечего, день всё равно считается отработанным."""
    cfg = core.cfg
    now = now or datetime.now()
    if not cfg.get("morning_brief", True) or not cfg.get("notify", True):
        return False
    if now.hour < int(cfg.get("morning_hour", 8)) or last_sent() == now.date().isoformat():
        return False
    payload = morning_payload(core, now.date())
    mark_sent(now.date().isoformat())
    if not payload["message"]:
        return False
    return apple.notify("Memory Desk · сегодня", payload["message"])
