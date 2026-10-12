"""Ближайшая встреча из календаря: ICS-файл или ICS-ссылка (Outlook, Google, iCloud, macOS Calendar → экспорт)."""
from __future__ import annotations

import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

_cache: dict[str, tuple[float, str]] = {}
CACHE_SECONDS = 300


def _load(source: str) -> str:
    cached = _cache.get(source)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    if source.startswith(("http://", "https://", "webcal://")):
        url = source.replace("webcal://", "https://", 1)
        request = urllib.request.Request(url, headers={"User-Agent": "MemoryDesk/2"})
        with urllib.request.urlopen(request, timeout=8) as response:
            text = response.read().decode("utf-8", errors="replace")
    else:
        text = Path(source).expanduser().read_text(encoding="utf-8", errors="replace")
    _cache[source] = (time.time(), text)
    return text


def _name(attendee) -> str:
    cn = getattr(attendee, "params", {}).get("CN")
    if cn:
        return str(cn)
    address = str(attendee).split(":", 1)[-1]
    return address.split("@", 1)[0].replace(".", " ").title()


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    return None  # события на весь день пропускаем


def upcoming(source: str, now: datetime | None = None) -> dict | None:
    """Идущая сейчас или ближайшая сегодняшняя встреча. None — если календарь не настроен/пуст/недоступен."""
    if not source.strip():
        return None
    try:
        import icalendar
        import recurring_ical_events
        calendar = icalendar.Calendar.from_ical(_load(source.strip()))
        now = now or datetime.now()
        day_start = datetime.combine(now.date(), datetime.min.time())
        events = recurring_ical_events.of(calendar).between(day_start, day_start + timedelta(days=1))
    except Exception:  # noqa: BLE001 — календарь необязателен, ошибка не должна ломать «Сегодня»
        return None
    found = []
    for event in events:
        start = _as_datetime(event.get("DTSTART").dt) if event.get("DTSTART") else None
        end_value = event.get("DTEND")
        end = _as_datetime(end_value.dt) if end_value else (start + timedelta(hours=1) if start else None)
        if not start or not end or end < now or str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        attendees = event.get("ATTENDEE") or []
        attendees = attendees if isinstance(attendees, list) else [attendees]
        names = list(dict.fromkeys(_name(a) for a in attendees))
        organizer = event.get("ORGANIZER")
        if organizer is not None and _name(organizer) not in names:
            names.insert(0, _name(organizer))
        found.append({
            "title": str(event.get("SUMMARY") or "Встреча"), "start": start.isoformat(timespec="minutes"),
            "end": end.isoformat(timespec="minutes"), "people": names[:8],
            "location": str(event.get("LOCATION") or ""), "ongoing": start <= now <= end,
        })
    found.sort(key=lambda item: item["start"])
    return found[0] if found else None
