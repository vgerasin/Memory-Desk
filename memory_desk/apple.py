"""Календарь и Напоминания macOS через EventKit (PyObjC).

Всё локально: доступ выдаёт сама macOS (окно «… хочет получить доступ к вашему календарю»).
Если PyObjC не установлен или система не macOS, бэкенд «недоступен», а остальное приложение работает как раньше.
Для тестов и демонстрации есть FakeBackend (MEMORY_DESK_FAKE_APPLE=1).
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import date, datetime

ACCESS_LABELS = {0: "notDetermined", 1: "restricted", 2: "denied", 3: "authorized", 4: "writeOnly"}


class AppleError(RuntimeError):
    pass


# --------------------------------------------------------------------------- EventKit

class EventKitBackend:
    """Реальный бэкенд. Создаётся только на macOS при установленном pyobjc-framework-EventKit."""

    def __init__(self) -> None:
        import EventKit  # noqa: PLC0415 — импорт нужен именно здесь, чтобы на других системах он не ломал запуск
        import Foundation  # noqa: PLC0415
        self.ek = EventKit
        self.fn = Foundation
        self.store = EventKit.EKEventStore.alloc().init()
        self.lock = threading.RLock()

    # --- доступ
    def status(self) -> dict:
        with self.lock:
            ek = self.ek
            return {
                "available": True, "reason": "",
                "events": ACCESS_LABELS.get(int(ek.EKEventStore.authorizationStatusForEntityType_(ek.EKEntityTypeEvent)), "unknown"),
                "reminders": ACCESS_LABELS.get(int(ek.EKEventStore.authorizationStatusForEntityType_(ek.EKEntityTypeReminder)), "unknown"),
            }

    def _request(self, kind: str) -> None:
        ek = self.ek
        done = threading.Event()

        def handler(_granted, _error):
            done.set()

        entity = ek.EKEntityTypeEvent if kind == "events" else ek.EKEntityTypeReminder
        full = "requestFullAccessToEventsWithCompletion_" if kind == "events" else "requestFullAccessToRemindersWithCompletion_"
        if hasattr(self.store, full):  # macOS 14+
            getattr(self.store, full)(handler)
        else:
            self.store.requestAccessToEntityType_completion_(entity, handler)
        done.wait(120)

    def request_access(self, kinds: tuple[str, ...] = ("events", "reminders")) -> dict:
        for kind in kinds:
            self._request(kind)
        self.refresh()
        return self.status()

    def refresh(self) -> None:
        with self.lock:
            self.store.reset()

    # --- преобразования
    def _ns(self, value: datetime):
        return self.fn.NSDate.dateWithTimeIntervalSince1970_(value.timestamp())

    @staticmethod
    def _dt(ns_date) -> datetime:
        return datetime.fromtimestamp(ns_date.timeIntervalSince1970())

    def _check(self, ok, error, action: str) -> None:
        if not ok:
            raise AppleError(f"{action}: {error.localizedDescription() if error else 'неизвестная ошибка'}")

    def _event(self, ev) -> dict:
        start, end = self._dt(ev.startDate()), self._dt(ev.endDate())
        attendees = []
        for person in ev.attendees() or []:
            url = person.URL()
            email = str(url.resourceSpecifier() or "") if url else ""
            attendees.append({"name": str(person.name() or email.split("@")[0]), "email": email,
                              "me": bool(person.isCurrentUser())})
        organizer = ev.organizer()
        return {
            "id": f"{ev.eventIdentifier()}@{int(start.timestamp())}",
            "title": str(ev.title() or "Встреча"), "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"), "allDay": bool(ev.isAllDay()),
            "calendar": str(ev.calendar().title()) if ev.calendar() else "", "location": str(ev.location() or ""),
            "notes": str(ev.notes() or ""), "attendees": attendees,
            "organizer": str(organizer.name()) if organizer and organizer.name() else None,
            "canceled": int(ev.status()) == 3,
        }

    # --- события
    def events(self, start: datetime, end: datetime) -> list[dict]:
        with self.lock:
            predicate = self.store.predicateForEventsWithStartDate_endDate_calendars_(self._ns(start), self._ns(end), None)
            items = self.store.eventsMatchingPredicate_(predicate) or []
            return sorted((self._event(ev) for ev in items), key=lambda e: e["start"])

    def create_event(self, title: str, start: datetime, end: datetime, *, all_day: bool = False,
                     notes: str = "", url: str = "", calendar: str = "") -> str:
        ek = self.ek
        with self.lock:
            ev = ek.EKEvent.eventWithEventStore_(self.store)
            ev.setTitle_(title)
            ev.setStartDate_(self._ns(start))
            ev.setEndDate_(self._ns(end))
            ev.setAllDay_(all_day)
            if notes:
                ev.setNotes_(notes)
            if url:
                ev.setURL_(self.fn.NSURL.URLWithString_(url))
            target = None
            for cal in self.store.calendarsForEntityType_(ek.EKEntityTypeEvent) or []:
                if calendar and str(cal.title()) == calendar and cal.allowsContentModifications():
                    target = cal
                    break
            ev.setCalendar_(target or self.store.defaultCalendarForNewEvents())
            ok, error = self.store.saveEvent_span_commit_error_(ev, ek.EKSpanThisEvent, True, None)
            self._check(ok, error, "Не удалось создать событие")
            return str(ev.eventIdentifier())

    # --- напоминания
    def _reminder_list(self, title: str):
        ek = self.ek
        for cal in self.store.calendarsForEntityType_(ek.EKEntityTypeReminder) or []:
            if str(cal.title()) == title and cal.allowsContentModifications():
                return cal
        cal = ek.EKCalendar.calendarForEntityType_eventStore_(ek.EKEntityTypeReminder, self.store)
        cal.setTitle_(title)
        default = self.store.defaultCalendarForNewReminders()
        source = default.source() if default else None
        if source is None:
            source = next(iter(self.store.sources() or []), None)
        if source is None:
            raise AppleError("Не найден аккаунт для списка напоминаний")
        cal.setSource_(source)
        ok, error = self.store.saveCalendar_commit_error_(cal, True, None)
        self._check(ok, error, "Не удалось создать список напоминаний")
        return cal

    def _components(self, due: date):
        comps = self.fn.NSDateComponents.alloc().init()
        comps.setYear_(due.year)
        comps.setMonth_(due.month)
        comps.setDay_(due.day)
        return comps

    def create_reminder(self, list_title: str, title: str, due: date | None, notes: str = "", url: str = "") -> str:
        with self.lock:
            reminder = self.ek.EKReminder.reminderWithEventStore_(self.store)
            reminder.setTitle_(title)
            reminder.setCalendar_(self._reminder_list(list_title))
            if notes:
                reminder.setNotes_(notes)
            if url:
                reminder.setURL_(self.fn.NSURL.URLWithString_(url))
            if due:
                reminder.setDueDateComponents_(self._components(due))
            ok, error = self.store.saveReminder_commit_error_(reminder, True, None)
            self._check(ok, error, "Не удалось создать напоминание")
            return str(reminder.calendarItemIdentifier())

    def reminder_state(self, reminder_id: str) -> dict | None:
        with self.lock:
            item = self.store.calendarItemWithIdentifier_(reminder_id)
            if item is None:
                return None
            comps = item.dueDateComponents()
            due = None
            if comps is not None and comps.year() > 0:
                due = date(int(comps.year()), int(comps.month()), int(comps.day()))
            return {"id": reminder_id, "completed": bool(item.isCompleted()), "title": str(item.title() or ""), "due": due}

    def update_reminder(self, reminder_id: str, *, title: str | None = None, due: date | None | str = "keep",
                        notes: str | None = None, completed: bool | None = None) -> None:
        with self.lock:
            item = self.store.calendarItemWithIdentifier_(reminder_id)
            if item is None:
                raise AppleError("Напоминание не найдено")
            if title is not None:
                item.setTitle_(title)
            if notes is not None:
                item.setNotes_(notes)
            if due != "keep":
                item.setDueDateComponents_(self._components(due) if due else None)
            if completed is not None:
                item.setCompleted_(completed)
            ok, error = self.store.saveReminder_commit_error_(item, True, None)
            self._check(ok, error, "Не удалось обновить напоминание")

    def delete_reminder(self, reminder_id: str) -> None:
        with self.lock:
            item = self.store.calendarItemWithIdentifier_(reminder_id)
            if item is None:
                return
            ok, error = self.store.removeReminder_commit_error_(item, True, None)
            self._check(ok, error, "Не удалось удалить напоминание")


# --------------------------------------------------------------------------- заглушки

class UnavailableBackend:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def status(self) -> dict:
        return {"available": False, "reason": self.reason, "events": "unavailable", "reminders": "unavailable"}

    def request_access(self, kinds=("events", "reminders")) -> dict:
        return self.status()

    def refresh(self) -> None:
        return None

    def _fail(self, *_a, **_k):
        raise AppleError(self.reason)

    events = create_event = create_reminder = reminder_state = update_reminder = delete_reminder = _fail


class FakeBackend:
    """Память вместо EventKit: для тестов и показа интерфейса без Mac."""

    def __init__(self, access: str = "authorized") -> None:
        self.access = access
        self.event_list: list[dict] = []
        self.reminders: dict[str, dict] = {}
        self.created_events: list[dict] = []
        self._n = 0

    def status(self) -> dict:
        return {"available": True, "reason": "", "events": self.access, "reminders": self.access}

    def request_access(self, kinds=("events", "reminders")) -> dict:
        if self.access == "notDetermined":
            self.access = "authorized"
        return self.status()

    def refresh(self) -> None:
        return None

    def _need(self) -> None:
        if self.access != "authorized":
            raise AppleError("Нет доступа")

    def add_event(self, title: str, start: datetime, end: datetime, attendees: list[str] | None = None, **extra) -> dict:
        self._n += 1
        event = {"id": f"ev{self._n}@{int(start.timestamp())}", "title": title, "start": start.isoformat(timespec="seconds"),
                 "end": end.isoformat(timespec="seconds"), "allDay": False, "calendar": "Работа", "location": "", "notes": "",
                 "attendees": [{"name": n, "email": "", "me": n == "Я"} for n in (attendees or [])],
                 "organizer": None, "canceled": False, **extra}
        self.event_list.append(event)
        return event

    def events(self, start: datetime, end: datetime) -> list[dict]:
        self._need()
        return sorted((e for e in self.event_list
                       if datetime.fromisoformat(e["end"]) >= start and datetime.fromisoformat(e["start"]) <= end),
                      key=lambda e: e["start"])

    def create_event(self, title, start, end, *, all_day=False, notes="", url="", calendar="") -> str:
        self._need()
        event = self.add_event(title, start, end, allDay=all_day, notes=notes, url=url)
        self.created_events.append(event)
        return event["id"]

    def create_reminder(self, list_title, title, due, notes="", url="") -> str:
        self._need()
        self._n += 1
        rid = f"rem{self._n}"
        self.reminders[rid] = {"id": rid, "list": list_title, "title": title, "due": due, "notes": notes, "url": url, "completed": False}
        return rid

    def reminder_state(self, reminder_id):
        self._need()
        item = self.reminders.get(reminder_id)
        return dict(item) if item else None

    def update_reminder(self, reminder_id, *, title=None, due="keep", notes=None, completed=None) -> None:
        self._need()
        item = self.reminders[reminder_id]
        if title is not None:
            item["title"] = title
        if notes is not None:
            item["notes"] = notes
        if due != "keep":
            item["due"] = due
        if completed is not None:
            item["completed"] = completed

    def delete_reminder(self, reminder_id) -> None:
        self._need()
        self.reminders.pop(reminder_id, None)


# --------------------------------------------------------------------------- выбор бэкенда

_backend = None
_backend_lock = threading.Lock()


def backend():
    global _backend
    with _backend_lock:
        if _backend is None:
            if os.environ.get("MEMORY_DESK_FAKE_APPLE"):
                _backend = FakeBackend()
            elif sys.platform != "darwin":
                _backend = UnavailableBackend("Доступно только на macOS")
            else:
                try:
                    _backend = EventKitBackend()
                except ImportError:
                    _backend = UnavailableBackend(
                        "Не установлен пакет pyobjc-framework-EventKit: он поставится при следующем запуске start.command")
                except Exception as exc:  # noqa: BLE001
                    _backend = UnavailableBackend(f"EventKit недоступен: {exc}")
        return _backend


def use_backend(new) -> None:
    """Подмена бэкенда (тесты)."""
    global _backend
    with _backend_lock:
        _backend = new


def to_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None

