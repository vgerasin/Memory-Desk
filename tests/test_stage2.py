"""Этап 2: утренняя сводка, зависшие задачи, серии встреч, расхождения между решениями."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    # модули приложения читают окружение при импорте, поэтому импортируем их только после фикстуры `fixture_dir`
    import memory_desk.apple, memory_desk.daily, memory_desk.storage  # noqa: F401
    g = globals()
    g["apple"], g["daily"], g["storage"] = memory_desk.apple, memory_desk.daily, memory_desk.storage


def set_task(client, task_id, **fields):
    """Статус/срок/ответственный — через API, «возраст» — прямой правкой updated_at."""
    age_days = fields.pop("age_days", None)
    if fields:
        assert client.patch(f"/api/tasks/{task_id}", json=fields).status_code == 200
    if age_days is not None:
        stamp = (datetime.now() - timedelta(days=age_days)).isoformat(timespec="seconds")
        with storage.connect() as db:
            db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (stamp, task_id))


@pytest.fixture()
def tasks_state(client):
    """Возвращает исходное состояние задач после теста."""
    before = {t["id"]: t for t in client.get("/api/tasks").json()["items"]}
    with storage.connect() as db:
        stamps = {r["id"]: r["updated_at"] for r in db.execute("SELECT id, updated_at FROM tasks")}
    yield list(before.values())
    for task in before.values():
        client.patch(f"/api/tasks/{task['id']}", json={"status": task["status"], "owner": task["owner"] or "",
                                                       "deadline": task["due"] or "не указан"})
    with storage.connect() as db:
        for task_id, stamp in stamps.items():
            db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (stamp, task_id))


# --------------------------------------------------------------------------- 2.2 зависшие задачи

def test_stuck_fields_rules():
    from memory_desk import daily
    today = date(2026, 10, 13)
    old = {"status": "open", "deadline": "не указан", "updated_at": "2026-09-20T10:00:00"}
    info = daily.stuck_fields(old, "2026-09-10", today)
    assert info["stuck"] and info["ageDays"] == 23 and info["stuckReasons"] == ["без движения 23 дн.", "без срока"]
    assert not daily.stuck_fields({**old, "updated_at": "2026-10-05T10:00:00"}, "2026-09-10", today)["stuck"]  # правка свежая
    assert not daily.stuck_fields({**old, "status": "draft"}, "2026-09-10", today)["stuck"]                    # черновик не «зависший»
    assert not daily.stuck_fields({**old, "status": "done"}, "2026-09-10", today)["stuck"]
    assert daily.stuck_fields({**old, "deadline": "2026-10-01"}, "2026-09-10", today)["stuckReasons"] == ["без движения 23 дн."]
    assert not daily.stuck_fields({**old, "updated_at": "2026-09-01T10:00:00"}, "2026-10-12", today)["stuck"]  # запись свежая


@pytest.fixture()
def aged_meeting(client):
    """Делает запись «старой» (дата в базе), потом возвращает как было."""
    saved = {}

    def age(meeting_id, days):
        row = storage.one("SELECT meeting_date FROM meetings WHERE id=?", (meeting_id,))
        saved.setdefault(meeting_id, row["meeting_date"])
        with storage.connect() as db:
            db.execute("UPDATE meetings SET meeting_date=? WHERE id=?", ((date.today() - timedelta(days=days)).isoformat(), meeting_id))

    yield age
    with storage.connect() as db:
        for meeting_id, value in saved.items():
            db.execute("UPDATE meetings SET meeting_date=? WHERE id=?", (value, meeting_id))


def test_tasks_api_marks_stuck(client, tasks_state, aged_meeting):
    by_meeting = {}
    for task in tasks_state:
        by_meeting.setdefault(task["meetingId"], []).append(task)
    old_meeting, fresh_meeting = [tasks for tasks in by_meeting.values() if len(tasks) >= 1][:2]
    aged_meeting(old_meeting[0]["meetingId"], 40)
    set_task(client, old_meeting[0]["id"], status="open", age_days=30)
    set_task(client, fresh_meeting[0]["id"], status="open", age_days=2)
    items = {t["id"]: t for t in client.get("/api/tasks").json()["items"]}
    stuck = items[old_meeting[0]["id"]]
    assert stuck["stuck"] and stuck["ageDays"] >= 30 and stuck["stuckReasons"][0].startswith("без движения")
    assert items[fresh_meeting[0]["id"]]["stuck"] is False
    # черновик той же старой записи не считается зависшим
    if len(old_meeting) > 1:
        assert items[old_meeting[1]["id"]]["stuck"] is False


# --------------------------------------------------------------------------- 2.1 утренняя сводка

@pytest.fixture()
def fake_apple(client, monkeypatch):
    from memory_desk import integrations
    fake = apple.FakeBackend()
    apple.use_backend(fake)
    monkeypatch.setattr(integrations, "schedule_sync", lambda cfg: None)
    cfg = client.get("/api/settings").json()
    cfg.pop("exists", None)
    original = dict(cfg)

    def configure(**values):
        cfg.update(values)
        assert client.put("/api/settings", json=cfg).status_code == 200

    yield fake, configure
    client.put("/api/settings", json=original)
    apple.use_backend(None)


def iso_in(days):
    return (date.today() + timedelta(days=days)).isoformat()


def test_morning_sections(client, tasks_state, fake_apple):
    fake, configure = fake_apple
    for task in tasks_state:  # чистый лист: ничего не в работе
        set_task(client, task["id"], status="draft")
    a, b, c, d = tasks_state[:4]
    set_task(client, a["id"], status="open", deadline=iso_in(-2), owner="Анна")
    set_task(client, b["id"], status="open", deadline=iso_in(2), owner="Вы")
    set_task(client, c["id"], status="open", deadline=iso_in(3), owner="Михаил")
    set_task(client, d["id"], status="open", deadline=iso_in(20), owner="Вы")      # дальше недели — не попадает
    start = datetime.now() + timedelta(minutes=2)
    fake.add_event("Синхронизация по КХД", start, start + timedelta(minutes=30), ["Я", "Анна"])
    configure(apple_calendar=True)

    data = client.get("/api/morning").json()
    assert data["counts"] == {"overdue": 1, "mine": 1, "promised": 1, "meetings": 1, "stuck": 0}
    assert [t["id"] for t in data["overdue"]] == [a["id"]] and data["overdue"][0]["owner"] == "Анна"
    assert [t["id"] for t in data["mine"]] == [b["id"]] and [t["id"] for t in data["promised"]] == [c["id"]]
    meeting = data["meetings"][0]
    assert meeting["title"] == "Синхронизация по КХД" and meeting["project"]["title"] == "КХД" and "." in meeting["headline"]
    assert data["empty"] is False
    assert data["message"] == "Просрочена 1 задача, сегодня 1 встреча, ваших на неделе: 1, вам обещали на неделе: 1"


def test_morning_empty_and_message():
    from memory_desk import daily
    assert daily.brief_message({"overdue": 0, "meetings": 0, "mine": 0, "promised": 0}) == ""
    assert daily.brief_message({"overdue": 5, "meetings": 2}) == "Просрочено 5 задач, сегодня 2 встречи"
    assert daily.brief_message({"overdue": 21, "meetings": 11}) == "Просрочена 21 задача, сегодня 11 встреч"


def test_morning_notification_once_a_day(client, tasks_state, monkeypatch):
    monkeypatch.setenv("MEMORY_DESK_FAKE_APPLE", "1")
    core = client.app.state.core
    for task in tasks_state:
        set_task(client, task["id"], status="draft")
    set_task(client, tasks_state[0]["id"], status="open", deadline=iso_in(-1), owner="Вы")
    saved = dict(core.cfg)
    noon = datetime.now().replace(hour=12, minute=0)
    apple.sent_notifications.clear()
    try:
        daily._state_path().unlink(missing_ok=True)
        assert daily.send_morning_notification(core, noon.replace(hour=7)) is False   # раньше заданного часа (8)
        assert apple.sent_notifications == []
        assert daily.send_morning_notification(core, noon) is True
        assert apple.sent_notifications[-1] == ("Memory Desk · сегодня", "Просрочена 1 задача")
        assert daily.send_morning_notification(core, noon) is False                   # второй раз в тот же день — нет
        daily._state_path().unlink()
        core.cfg["morning_brief"] = False
        assert daily.send_morning_notification(core, noon) is False                   # выключено в настройках
        core.cfg.update(morning_brief=True, notify=False)
        assert daily.send_morning_notification(core, noon) is False                   # уведомления выключены целиком
        core.cfg.update(notify=True, morning_hour=13)
        assert daily.send_morning_notification(core, noon) is False                   # час ещё не наступил
    finally:
        core.cfg.update(saved)
        daily._state_path().unlink(missing_ok=True)


def test_morning_silent_when_nothing_to_say(client, tasks_state, monkeypatch):
    monkeypatch.setenv("MEMORY_DESK_FAKE_APPLE", "1")
    core = client.app.state.core
    for task in tasks_state:
        set_task(client, task["id"], status="draft")
    apple.sent_notifications.clear()
    try:
        assert daily.send_morning_notification(core, datetime.now().replace(hour=12)) is False
        assert apple.sent_notifications == []
        assert daily.last_sent() == date.today().isoformat()  # день отработан: сегодня больше не проверяем
    finally:
        daily._state_path().unlink(missing_ok=True)


def test_morning_settings_validated(client):
    cfg = client.get("/api/settings").json()
    base = {k: cfg[k] for k in ("recordings_dir", "obsidian_dir", "archive_dir")}
    ok = client.put("/api/settings", json={**base, "morning_hour": 99, "morning_brief": False}).json()
    assert ok["morning_hour"] == 23 and ok["morning_brief"] is False
    client.put("/api/settings", json={**base})
    assert client.get("/api/settings").json()["morning_hour"] == 8


def test_tasks_registry_stuck_filter(client, tasks_state, aged_meeting):
    first = tasks_state[0]
    set_task(client, first["id"], status="open", age_days=45)
    aged_meeting(first["meetingId"], 50)
    plain = client.get("/api/tasks").json()
    stuck = client.get("/api/tasks", params={"stuck": "true"}).json()
    assert plain["counts"]["stuck"] == stuck["total"] >= 1
    assert first["id"] in [t["id"] for t in stuck["items"]] and all(t["stuck"] for t in stuck["items"])
    ages = [t["ageDays"] for t in stuck["items"]]
    assert ages == sorted(ages, reverse=True)
    assert plain["total"] > stuck["total"]


# --------------------------------------------------------------------------- 2.3 серии встреч

class NoteFactory:
    """Дописывает в хранилище фикстуры заметки в формате пайплайна и убирает их после теста."""

    def __init__(self, client):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import meetings_process as mp
        self.mp, self.client, self.paths = mp, client, []
        self.vault = Path(client.get("/api/settings").json()["obsidian_dir"])

    def add(self, days_ago, title, *, project="КХД", people=(), questions=(), decisions=(), tasks=(), hour=10):
        from pathlib import Path
        recorded = (datetime.now() - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0, microsecond=0)
        data = {"title": title, "summary": [f"Обсудили {title}"], "topics": [], "proposals": [],
                "decisions": [{"text": t, "evidence_time": "00:01:00"} for t in decisions],
                "tasks": [{"task": t, "owner": o, "deadline": "не указан", "evidence_time": "00:02:00"} for t, o in tasks],
                "risks": [], "open_questions": [{"text": q, "evidence_time": "00:03:00"} for q in questions], "next_steps": []}
        note = self.mp.render_note(Path(f"PLAUD_{recorded:%Y%m%d_%H%M}.mp3"), recorded, 600, data, "x — стенограмма.md")
        if project:
            pid = {"КХД": "kxd", "Личное": "lichnoe"}[project]
            note = note.replace('project_id: ""', f'project_id: "{pid}"\nproject: "{project}"')
            note = note.replace("source_type: Не указан", "source_type: Очная встреча")
        if people:
            note = note.replace("status: processed\n", "participants:\n" + "".join(f'  - "{n}"\n' for n in people) + "status: processed\n", 1)
        folder = self.vault / "series-test"
        folder.mkdir(exist_ok=True)
        path = folder / f"{recorded:%Y-%m-%d %H-%M} — {self.mp.slug(title)}.md"
        path.write_text(note, encoding="utf-8")
        self.paths.append(path)
        return path

    def reload(self):
        self.client.app.state.core.reload(force=True)

    def id_of(self, title_part, days_ago):
        day = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        items = self.client.get("/api/meetings", params={"limit": 200}).json()["items"]
        return next(m["id"] for m in items if title_part in m["title"] and m["date"] == day)


@pytest.fixture()
def notes(client):
    factory = NoteFactory(client)
    yield factory
    for path in factory.paths:
        path.unlink(missing_ok=True)
    try:
        (factory.vault / "series-test").rmdir()
    except OSError:
        pass
    factory.reload()


def test_title_tokens_and_similarity():
    from memory_desk import series
    a, b = series.tokens("Планёрка КХД 12.10"), series.tokens("Планерка по КХД")
    assert series.similarity(a, b) >= 0.6
    assert series.similarity(series.tokens("Идеи для заметки о привычках"), a) == 0
    assert series.tokens("Анна") == series.tokens("Анной")  # грубая основа: падежи не разводят записи


def test_series_changes_and_repeated_questions(client, notes):
    q = "Где хранить историю изменений справочников?"
    notes.add(28, "Планёрка по КХД", questions=[q], tasks=[("Собрать требования", "Анна")], decisions=["Начать с витрины продаж"])
    notes.add(21, "Планёрка КХД", questions=[q, "Кто принимает приёмку?"], tasks=[("Оценить объём истории", "Михаил")])
    notes.add(14, "Планёрка по КХД 14.10", questions=["Где хранить историю изменений справочников"], decisions=["Хранить историю в отдельной схеме"])
    notes.add(6, "Покупки на неделю", project="Личное")
    notes.reload()

    last = notes.id_of("Планёрка по КХД 14", 14)
    first = notes.id_of("Планёрка по КХД", 28)
    data = client.get(f"/api/meetings/{last}/series").json()["series"]
    assert data["count"] == 3 and data["position"] == 3 and data["previous"]["date"] == (datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d")
    assert [d["text"] for d in data["decisionsNew"]] == ["Хранить историю в отдельной схеме"]
    assert [x["text"] for x in data["questionsResolved"]] == ["Кто принимает приёмку?"]       # в прошлый раз был, теперь нет
    assert data["questionsNew"] == []
    repeated = data["questionsRepeated"]
    assert len(repeated) == 1 and repeated[0]["count"] == 3 and repeated[0]["since"] == (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")

    one = client.get(f"/api/meetings/{first}/series").json()["series"]
    assert one["position"] == 1 and one["previous"] is None and one["questionsRepeated"] == []

    # прошлые задачи: что закрыто и что ещё висит
    prev_task = next(t for t in client.get("/api/tasks").json()["items"] if t["text"] == "Оценить объём истории")
    assert client.patch(f"/api/tasks/{prev_task['id']}", json={"status": "done"}).status_code == 200
    again = client.get(f"/api/meetings/{last}/series").json()["series"]
    assert [t["text"] for t in again["tasksClosed"]] == ["Оценить объём истории"]

    habit = notes.id_of("Идеи для заметки", 7)      # одинокая запись фикстуры: серии нет
    assert client.get(f"/api/meetings/{habit}/series").json()["series"] is None
    listing = client.get("/api/series").json()["items"]
    kxd = next(s for s in listing if s["project"] == "КХД")
    assert kxd["count"] == 3 and kxd["repeatedQuestions"] == 1 and kxd["latestId"] == last
    assert client.get("/api/meetings/нет/series").status_code == 404


def test_series_same_people_relaxes_title_match(client, notes):
    notes.add(10, "Еженедельная синхронизация", people=["Анна Соколова"])
    notes.add(3, "Созвон по текущим делам", people=["Анна Соколова"])      # другое название, те же люди → тоже серия? нет: порог 0.4, слова не пересекаются
    notes.add(5, "Еженедельная синхронизация команды", people=["Анна Соколова"])
    notes.reload()
    items = client.get("/api/series").json()["items"]
    series = [s for s in items if "синхрон" in s["title"].casefold()]
    assert len(series) == 1 and series[0]["count"] == 2


# --------------------------------------------------------------------------- 2.4 расхождения между решениями

@pytest.fixture()
def clean_dismissed(client):
    storage.set_setting("dismissed_conflicts", "[]")
    yield
    storage.set_setting("dismissed_conflicts", "[]")


def test_conflict_candidates_flow(client, notes, clean_dismissed, monkeypatch):
    old_text = "Перейти на инкрементальную загрузку витрин продаж"
    notes.add(2, "Пересмотр загрузки витрин", decisions=["Отказаться от инкрементальной загрузки витрин продаж и вернуться к полной перезагрузке",
                                                          "Закупить два новых монитора для отдела"])
    notes.add(1, "Другой проект: заметки", project="Личное", decisions=["Отказаться от инкрементальной загрузки витрин продаж"])
    notes.reload()

    data = client.get("/api/decisions/conflicts").json()
    pairs = [p for p in data["items"] if p["old"]["text"] == old_text]
    assert len(pairs) == 1, data           # монитор — другой вопрос; решение из другого проекта не в счёт
    pair = pairs[0]
    assert pair["new"]["text"].startswith("Отказаться от инкрементальной загрузки витрин продаж и вернуться") and pair["project"] == "КХД"
    assert pair["old"]["meetingDate"] < pair["new"]["meetingDate"] and 0.34 <= pair["similarity"] <= 1

    # «Спросить модель»: подмена Ollama
    from memory_desk import llm
    asked = []

    def fake(prompt, **kwargs):
        asked.append(prompt)
        return {"relation": "replaces", "reason": "Новое решение отменяет переход на инкрементальную загрузку."}

    monkeypatch.setattr(llm, "ollama_json", fake)
    body = {"old": pair["old"]["id"], "new": pair["new"]["id"]}
    verdict = client.post("/api/decisions/conflicts/check", json=body).json()
    assert verdict["relation"] == "replaces" and "отменяет" in verdict["reason"]
    assert old_text in asked[0] and "Новое решение" in asked[0]
    monkeypatch.setattr(llm, "ollama_json", lambda *a, **k: {"relation": "что-то"})
    assert client.post("/api/decisions/conflicts/check", json=body).status_code == 503
    monkeypatch.setattr(llm, "ollama_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Ollama не отвечает")))
    assert client.post("/api/decisions/conflicts/check", json=body).status_code == 503
    assert client.post("/api/decisions/conflicts/check", json={"old": "нет", "new": "нет"}).status_code == 404

    # «Не связаны»: пара больше не предлагается
    assert client.post("/api/decisions/conflicts/dismiss", json=body).json() == {"dismissed": True}
    after = client.get("/api/decisions/conflicts").json()["items"]
    assert not [p for p in after if p["old"]["text"] == old_text]
    storage.set_setting("dismissed_conflicts", "[]")

    # «Старое заменено новым»: действует прежняя отметка «Заменено», пара исчезает из предложений
    assert client.patch(f"/api/decisions/{pair['old']['id']}", json={"replaced_by": pair["new"]["id"]}).status_code == 200
    assert not [p for p in client.get("/api/decisions/conflicts").json()["items"] if p["old"]["text"] == old_text]
    client.patch(f"/api/decisions/{pair['old']['id']}", json={"replaced_by": ""})
