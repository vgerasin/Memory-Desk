"""Этап 7: предложения действий после встречи (событие, письмо, напоминание) и разбор русских дат."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SPEECH = (
    "[00:00:01] **Спикер 2:** Вчера мы всё обсудили и разошлись.\n\n"
    "[00:00:05] **Спикер 1:** Подведём итог. Давайте созвонимся в четверг в 15:00.\n\n"
    "[00:00:20] **Спикер 2:** Хорошо, договорились.\n\n"
    "[00:01:10] **Спикер 1:** Анна, отправь протокол Ивану после встречи.\n\n"
    "[00:01:40] **Спикер 2:** Напомни мне проверить доступ до пятницы.\n\n"
    "[00:02:10] **Спикер 1:** Может быть, в следующем месяце созвонимся.\n\n"
    "[00:02:30] **Спикер 1:** Через пару дней ещё раз созвонимся.\n\n"
    "[00:03:00] **Спикер 2:** Спасибо всем, на этом всё.\n"
)


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import meetings_process
    import memory_desk.apple, memory_desk.audit, memory_desk.mailer, memory_desk.storage, memory_desk.suggest  # noqa: F401
    import memory_desk.knowledge, memory_desk.whenru  # noqa: F401
    import test_stage3 as s3
    s3.mp, s3.knowledge = meetings_process, memory_desk.knowledge
    globals().update(apple=memory_desk.apple, audit=memory_desk.audit, mailer=memory_desk.mailer, storage=memory_desk.storage,
                     suggest=memory_desk.suggest, whenru=memory_desk.whenru, s3=s3)


@pytest.fixture()
def note(client, tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(s3, "TRANSCRIPT", SPEECH)
    created = s3.Note(client).create()
    storage.set_meeting_people(created.id, ["Анна Петрова", "Иван Сидоров"])
    storage.update_item("people", storage.one("SELECT id FROM people WHERE name=?", ("Иван Сидоров",))["id"], notes="почта ivan@example.com")
    yield created
    storage.initialize()
    with storage.connect() as db:
        db.execute("DELETE FROM suggestions WHERE meeting_id=?", (created.id,))
    created.remove()


@pytest.fixture()
def fakes(monkeypatch):
    calendar, post = apple.FakeBackend(), mailer.FakeMail()
    apple.use_backend(calendar)
    mailer.use_backend(post)
    yield calendar, post
    apple.use_backend(None)
    mailer.use_backend(None)


def model(*items):
    return lambda prompt: {"items": list(items)}


def good_model():
    return model(
        {"kind": "event", "evidence_time": "00:00:05", "title": "Созвон по справочнику", "addressee": "", "confidence": 0.9},
        {"kind": "mail", "evidence_time": "00:01:10", "title": "", "addressee": "Ивану", "confidence": 0.8},
        {"kind": "reminder", "evidence_time": "00:01:40", "title": "Проверить доступ", "addressee": "", "confidence": 0.85},
        {"kind": "event", "evidence_time": "00:02:30", "title": "Ещё один созвон", "addressee": "", "confidence": 0.9},
        # мусор, который обязан отсеяться
        {"kind": "event", "evidence_time": "00:09:59", "title": "Выдуманный таймкод", "confidence": 0.95},
        {"kind": "reminder", "evidence_time": "00:01:40", "title": "Слишком неуверенно", "confidence": 0.3},
        {"kind": "sms", "evidence_time": "00:01:10", "title": "Неизвестный вид", "confidence": 0.9},
        {"kind": "event", "evidence_time": "00:02:10", "title": "Предположение", "confidence": 0.9},
        {"kind": "event", "evidence_time": "00:00:01", "title": "Прошлое", "confidence": 0.9},
        {"kind": "event", "evidence_time": "00:00:20", "title": "Сосед без признаков", "confidence": 0.9},
        {"kind": "mail", "evidence_time": "00:01:40", "title": "Письмо там, где его нет", "confidence": 0.9},
    )


def by_kind(cards, kind, t=None):
    return next(c for c in cards if c["kind"] == kind and (t is None or c["t"] == t))


# --------------------------------------------------------------------------- разбор дат

BASE = date(2026, 10, 13)      # вторник


@pytest.mark.parametrize("text,day,clock", [
    ("Давайте созвонимся в четверг в 15:00", date(2026, 10, 15), "15:00"),
    ("в среду", date(2026, 10, 14), None),
    ("во вторник", date(2026, 10, 20), None),                  # сказано во вторник: следующий
    ("завтра в 9 утра", date(2026, 10, 14), "09:00"),
    ("послезавтра", date(2026, 10, 15), None),
    ("через неделю в три часа дня", date(2026, 10, 20), "15:00"),
    ("через две недели", date(2026, 10, 27), None),
    ("через 3 дня", date(2026, 10, 16), None),
    ("через месяц", date(2026, 11, 13), None),
    ("до пятницы", date(2026, 10, 16), None),
    ("15 октября в 10:30", date(2026, 10, 15), "10:30"),
    ("20 октября", date(2026, 10, 20), None),
    ("5 июня", date(2027, 6, 5), None),
    ("к 3.11", date(2026, 11, 3), None),
    ("12.12.2026", date(2026, 12, 12), None),
    ("до 20 числа", date(2026, 10, 20), None),
    ("на следующей неделе во вторник", date(2026, 10, 20), None),
    ("на этой неделе в пятницу", date(2026, 10, 16), None),
    ("в следующий вторник", date(2026, 10, 20), None),
    ("в субботу в 7 вечера", date(2026, 10, 17), "19:00"),
    ("в 12 ночи в среду", date(2026, 10, 14), "00:00"),
    ("в 15.30 встреча", None, "15:30"),
    ("в половине третьего", None, "14:30"),
    ("в 9:15 утра", None, "09:15"),
])
def test_dates_are_resolved_by_code(text, day, clock):
    got = whenru.resolve(text, BASE)
    assert got.day == day and (got.clock.strftime("%H:%M") if got.clock else None) == clock and not got.ambiguous


@pytest.mark.parametrize("text", [
    "в следующий четверг",            # во вторник: этот четверг или через неделю?
    "на этой неделе",
    "через пару дней",
    "в конце месяца",
    "в четверг в 15:00, а в пятницу в 12",
    "31 июня",
    "5 октября",                      # прошло недавно: год не додумываем
    "на следующей неделе",
])
def test_ambiguous_phrases_are_not_guessed(text):
    got = whenru.resolve(text, BASE)
    assert got.day is None and got.ambiguous


def test_non_dates_are_not_dates():
    for text in ("Анна, пришли протокол", "на 3 дня", "нас было 3 человека", "версия 2.1", "просто разговор"):
        got = whenru.resolve(text, BASE)
        assert got.day is None and not got.ambiguous, text


def test_weekday_calendar_is_correct_for_every_day():
    names = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
    for shift in range(30):
        base = BASE + timedelta(days=shift)
        for weekday, name in enumerate(names):
            got = whenru.resolve(f"в {name}", base)
            assert got.day.weekday() == weekday and 1 <= (got.day - base).days <= 7


def test_year_boundary():
    assert whenru.resolve("через неделю", date(2026, 12, 28)).day == date(2027, 1, 4)
    assert whenru.resolve("в пятницу", date(2026, 12, 30)).day == date(2027, 1, 1)
    assert whenru.resolve("через месяц", date(2026, 1, 31)).day == date(2026, 2, 28)
    assert whenru.resolve("3 января", date(2026, 12, 20)).day == date(2027, 1, 3)


# --------------------------------------------------------------------------- поиск предложений

def test_cards_have_source_resolved_dates_and_garbage_is_dropped(client, note):
    core = client.app.state.core
    result = suggest.scan(core, note.id, good_model())
    cards = suggest.for_meeting(note.id)["items"]
    assert result["count"] == len(cards) == 4 and result["engine"] == "model", [(c["title"], c["t"]) for c in cards]
    base = note.recorded.date()
    event = by_kind(cards, "event", "00:00:05")
    assert event["day"] == whenru.resolve("в четверг", base).day.isoformat() and event["clock"] == "15:00"
    assert event["needs"] == [] and event["quote"].startswith("Подведём итог") and event["title"] == "Созвон по справочнику"
    mail = by_kind(cards, "mail")
    assert mail["who"] == "Иван Сидоров" and mail["t"] == "00:01:10"
    reminder = by_kind(cards, "reminder")
    assert reminder["day"] == whenru.resolve("до пятницы", base).day.isoformat() and reminder["deadline"] is True
    vague = by_kind(cards, "event", "00:02:30")
    assert vague["day"] == "" and vague["needs"] == ["day", "time"] and vague["canCreate"] is False
    assert not any(c["title"] in ("Выдуманный таймкод", "Слишком неуверенно", "Неизвестный вид", "Предположение", "Прошлое", "Сосед без признаков", "Письмо там, где его нет") for c in cards)


def test_scan_changes_nothing_outside_the_registry(client, note, fakes):
    calendar, post = fakes
    before = note.path.read_bytes()
    suggest.scan(client.app.state.core, note.id, good_model())
    assert note.path.read_bytes() == before
    assert calendar.created_events == [] and post.drafts == []


def test_no_candidates_means_no_model_call(client, note, monkeypatch):
    monkeypatch.setattr(s3, "TRANSCRIPT", "[00:00:05] **Спикер 1:** Обсудили справочник.\n\n[00:00:20] **Спикер 2:** Да, всё понятно.\n")
    note.create()
    client.app.state.core.reload(force=True)

    def forbidden(prompt):
        raise AssertionError("модель вызвана без кандидатов")
    result = suggest.scan(client.app.state.core, note.id, forbidden)
    assert result == {"count": 0, "engine": "none"}


def test_model_is_given_only_candidate_lines_and_no_dates_are_asked(client, note):
    seen = []
    suggest.scan(client.app.state.core, note.id, lambda prompt: seen.append(prompt) or {"items": []})
    prompt = seen[0]
    assert "созвонимся в четверг" in prompt and "Вчера мы всё обсудили" not in prompt
    assert "Дат и времени НЕ называй" in prompt


def test_rules_work_when_model_is_unavailable_and_are_careful(client, note):
    def down(prompt):
        raise RuntimeError("Ollama не отвечает")
    result = suggest.scan(client.app.state.core, note.id, down)
    cards = suggest.for_meeting(note.id)["items"]
    assert result["engine"] == "rules" and cards and all(c["engine"] == "rules" and c["confidence"] <= 0.7 for c in cards)
    assert not any(c["t"] in ("00:02:10", "00:00:01") for c in cards)          # предположение и прошлое
    assert {c["kind"] for c in cards} == {"event", "mail", "reminder"}
    assert by_kind(cards, "mail")["who"] == "Иван Сидоров"          # «Анна, отправь протокол Ивану»: Анна — не получатель


def test_limit_of_cards_per_record(client, note, monkeypatch):
    lines = "\n\n".join(f"[00:{n // 60:02d}:{n % 60:02d}] **Спикер 1:** Давайте созвонимся в четверг в {9 + n % 8}:00." for n in range(5, 100, 10))
    monkeypatch.setattr(s3, "TRANSCRIPT", lines + "\n")
    note.create()
    client.app.state.core.reload(force=True)
    items = [{"kind": "event", "evidence_time": f"00:{n // 60:02d}:{n % 60:02d}", "title": f"Созвон {n}", "confidence": 0.9} for n in range(5, 100, 10)]
    suggest.scan(client.app.state.core, note.id, model(*items))
    assert len(suggest.for_meeting(note.id)["items"]) == suggest.MAX_CARDS


def test_rescan_keeps_decisions_of_the_person(client, note):
    core = client.app.state.core
    suggest.scan(core, note.id, good_model())
    cards = suggest.for_meeting(note.id)["items"]
    dismissed, edited = by_kind(cards, "reminder"), by_kind(cards, "event", "00:02:30")
    client.post(f"/api/suggestions/{dismissed['id']}/dismiss")
    client.patch(f"/api/suggestions/{edited['id']}", json={"title": "Мой заголовок"})
    suggest.scan(core, note.id, good_model())
    again = {c["id"]: c for c in suggest.for_meeting(note.id)["items"]}
    assert len(again) == 4 and again[dismissed["id"]]["status"] == "dismissed" and again[edited["id"]]["title"] == "Мой заголовок"
    suggest.scan(core, note.id, model())            # модель больше ничего не находит: неправленные новые убираются
    left = {c["id"] for c in suggest.for_meeting(note.id)["items"]}
    assert left == {dismissed["id"], edited["id"]}


# --------------------------------------------------------------------------- действия только по нажатию

def future(days=3) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_event_is_created_only_on_create_and_only_once(client, note, fakes):
    calendar, post = fakes
    suggest.scan(client.app.state.core, note.id, good_model())
    event = by_kind(suggest.for_meeting(note.id)["items"], "event", "00:00:05")
    client.patch(f"/api/suggestions/{event['id']}", json={"day": future(), "clock": "15:00"})
    assert calendar.created_events == []
    done = client.post(f"/api/suggestions/{event['id']}/create")
    assert done.status_code == 200 and done.json()["status"] == "created" and "Календарь" in done.json()["message"]
    made = calendar.created_events
    assert len(made) == 1 and made[0]["title"] == "Созвон по справочнику" and made[0]["start"].startswith(future())
    assert "Разговор о справочнике" in made[0].get("notes", "") and "Анна Петрова" in made[0].get("notes", "")
    assert client.post(f"/api/suggestions/{event['id']}/create").status_code == 409
    assert len(calendar.created_events) == 1
    assert client.patch(f"/api/suggestions/{event['id']}", json={"title": "x"}).status_code == 409
    assert client.post(f"/api/suggestions/{event['id']}/dismiss").status_code == 409


def test_event_without_date_or_in_the_past_is_refused(client, note, fakes):
    calendar, _ = fakes
    suggest.scan(client.app.state.core, note.id, good_model())
    vague = by_kind(suggest.for_meeting(note.id)["items"], "event", "00:02:30")
    answer = client.post(f"/api/suggestions/{vague['id']}/create")
    assert answer.status_code == 400 and "дату" in answer.json()["detail"]
    client.patch(f"/api/suggestions/{vague['id']}", json={"day": "2020-01-01", "clock": "10:00"})
    past = client.post(f"/api/suggestions/{vague['id']}/create")
    assert past.status_code == 400 and "прошла" in past.json()["detail"]
    assert calendar.created_events == []
    fixed = client.patch(f"/api/suggestions/{vague['id']}", json={"day": future(5), "clock": "11:30"}).json()
    assert fixed["canCreate"] is True and fixed["needs"] == [] and fixed["edited"] is True
    assert client.post(f"/api/suggestions/{vague['id']}/create").status_code == 200


def test_edit_validation(client, note):
    suggest.scan(client.app.state.core, note.id, good_model())
    sid = by_kind(suggest.for_meeting(note.id)["items"], "event", "00:00:05")["id"]
    for body in ({"day": "завтра"}, {"clock": "25:61"}, {"title": "   "}, {}):
        assert client.patch(f"/api/suggestions/{sid}", json=body).status_code == 400
    assert client.patch("/api/suggestions/нет", json={"title": "x"}).status_code == 404
    assert client.post("/api/suggestions/нет/create").status_code == 404


def test_mail_is_only_a_draft_never_sent(client, note, fakes):
    _, post = fakes
    suggest.scan(client.app.state.core, note.id, good_model())
    mail = by_kind(suggest.for_meeting(note.id)["items"], "mail")
    assert post.drafts == []
    done = client.post(f"/api/suggestions/{mail['id']}/create")
    assert done.status_code == 200 and "отправьте сами" in done.json()["message"]
    assert len(post.drafts) == 1 and post.sent == []
    draft = post.drafts[0]
    assert draft["to"] == ["ivan@example.com"] and draft["subject"] == "Итоги: Разговор о справочнике"
    assert "Иван Сидоров, добрый день!" in draft["body"] and "Справочник ведём в КХД" in draft["body"]
    assert "**" not in draft["body"] and "# " not in draft["body"]


def test_reminder_goes_to_reminders_with_the_due_date(client, note, fakes):
    calendar, _ = fakes
    suggest.scan(client.app.state.core, note.id, good_model())
    reminder = by_kind(suggest.for_meeting(note.id)["items"], "reminder")
    client.patch(f"/api/suggestions/{reminder['id']}", json={"day": future(2)})
    assert client.post(f"/api/suggestions/{reminder['id']}/create").status_code == 200
    made = list(calendar.reminders.values())
    assert made and made[0]["title"] == "Проверить доступ" and str(made[0]["due"]) == future(2)
    assert made[0]["list"] == (client.app.state.core.cfg.get("reminders_list") or "Memory Desk")


def test_no_access_to_calendar_is_a_clear_error(client, note):
    apple.use_backend(apple.FakeBackend(access="denied"))
    try:
        suggest.scan(client.app.state.core, note.id, good_model())
        event = by_kind(suggest.for_meeting(note.id)["items"], "event", "00:00:05")
        client.patch(f"/api/suggestions/{event['id']}", json={"day": future(), "clock": "15:00"})
        answer = client.post(f"/api/suggestions/{event['id']}/create")
        assert answer.status_code == 409 and "Календар" in answer.json()["detail"]
        assert by_kind(suggest.for_meeting(note.id)["items"], "event", "00:00:05")["status"] == "new"
    finally:
        apple.use_backend(None)


def test_dismiss_and_restore(client, note, fakes):
    suggest.scan(client.app.state.core, note.id, good_model())
    sid = by_kind(suggest.for_meeting(note.id)["items"], "reminder")["id"]
    assert client.post(f"/api/suggestions/{sid}/dismiss").json()["status"] == "dismissed"
    assert client.post(f"/api/suggestions/{sid}/restore").json()["status"] == "new"
    pending = client.get("/api/suggestions").json()
    assert pending["total"] >= 4 and any(i["id"] == sid and i["meetingTitle"] for i in pending["items"])


# --------------------------------------------------------------------------- журнал и стирание

def test_actions_are_logged_without_text_of_the_record(client, note, fakes, tmp_path):
    suggest.scan(client.app.state.core, note.id, good_model(), actor="cabinet")
    event = by_kind(suggest.for_meeting(note.id)["items"], "event", "00:00:05")
    client.patch(f"/api/suggestions/{event['id']}", json={"day": future(), "clock": "15:00"})
    client.post(f"/api/suggestions/{event['id']}/create")
    mail = by_kind(suggest.for_meeting(note.id)["items"], "mail")
    client.post(f"/api/suggestions/{mail['id']}/create")
    rows = [json.loads(x) for x in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()]
    codes = [(r["action"], r["result"], r["detail"]) for r in rows]
    assert ("suggestion.scan", "ok") in [(c[0], c[1]) for c in codes]
    assert any(c[0] == "suggestion.create" and c[1] == "ok" and "Календар" in c[2] for c in codes)
    assert any(c[0] == "suggestion.create" and "письма" in c[2].lower() for c in codes)
    raw = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "Подведём итог" not in raw and "Созвон по справочнику" not in raw and "ivan@example.com" not in raw


def test_failed_creation_is_logged_as_error(client, note, tmp_path):
    suggest.scan(client.app.state.core, note.id, good_model())
    vague = by_kind(suggest.for_meeting(note.id)["items"], "event", "00:02:30")
    client.post(f"/api/suggestions/{vague['id']}/create")
    rows = [json.loads(x) for x in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()]
    assert any(r["action"] == "suggestion.create" and r["result"] == "error" for r in rows)


def test_erasing_the_record_erases_its_suggestions(client, note, fakes):
    suggest.scan(client.app.state.core, note.id, good_model())
    mail = by_kind(suggest.for_meeting(note.id)["items"], "mail")
    client.post(f"/api/suggestions/{mail['id']}/create")
    plan = client.get(f"/api/meetings/{note.id}/erase/plan").json()
    assert any("останутся там, где созданы" in w for w in plan["warnings"])
    done = client.post(f"/api/meetings/{note.id}/erase", json={"confirm": "СТЕРЕТЬ"}).json()
    assert done["ok"] is True
    assert storage.one("SELECT COUNT(*) n FROM suggestions WHERE meeting_id=?", (note.id,))["n"] == 0
    assert client.get(f"/api/meetings/{note.id}/suggestions").status_code == 404


def test_background_scan_reports_state(client, note):
    import time
    started = client.post(f"/api/meetings/{note.id}/suggestions/scan")
    assert started.status_code == 200
    for _ in range(100):
        state = client.get(f"/api/meetings/{note.id}/suggestions").json()["scan"]
        if state["state"] != "running":
            break
        time.sleep(0.1)
    assert state["state"] in ("done", "error")       # Ollama в тестах нет: сработали правила или поиск завершился ошибкой
    assert client.post("/api/meetings/нет/suggestions/scan").status_code == 404


def test_setting_can_turn_automatic_search_off(client):
    original = client.get("/api/settings").json()["suggestions_auto"]
    body = {k: v for k, v in client.get("/api/settings").json().items() if k not in ("exists", "lock_timeout_min")}
    body["suggestions_auto"] = False
    assert client.put("/api/settings", json=body).status_code == 200
    assert client.get("/api/settings").json()["suggestions_auto"] is False
    body["suggestions_auto"] = original
    client.put("/api/settings", json=body)
