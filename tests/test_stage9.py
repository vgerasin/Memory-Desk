"""Этап 9: живое досье человека и проекта: закреплённые факты, висящие вопросы, «о чём разговор», обновление по событию."""
from __future__ import annotations

import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PERSON = "Анна Петрова"
OTHER = "Иван Сидоров"

Q_HANG = "Кто отвечает за выгрузку справочника клиентов"
Q_HANG_2 = "Кто будет отвечать за выгрузку справочника клиентов"
Q_OLD = "Когда подключим архивное хранилище логов"
Q_ANSWERED = "Какого поставщика выбираем для лицензий"
Q_SINGLE = "Нужен ли отдельный стенд для нагрузочных проверок"


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import meetings_process
    import memory_desk.audit, memory_desk.digest, memory_desk.erase, memory_desk.knowledge, memory_desk.live, memory_desk.storage  # noqa: F401
    globals().update(mp=meetings_process, audit=memory_desk.audit, digest=memory_desk.digest, erase=memory_desk.erase,
                     knowledge=memory_desk.knowledge, live=memory_desk.live, storage=memory_desk.storage)


class Notes:
    """Несколько заметок в хранилище фикстуры; убираются после теста."""

    def __init__(self, client):
        self.client = client
        vault = Path(client.get("/api/settings").json()["obsidian_dir"])
        self.folder = vault / "dossier9"
        self.core = client.app.state.core
        self.made: list[str] = []
        self.counter = 0

    def add(self, title, days_ago, *, questions=(), risks=(), decisions=(), summary=("Обсуждали выгрузку справочника",), people=(PERSON, OTHER),
            project=True, tasks=()):
        self.counter += 1
        recorded = (datetime.now() - timedelta(days=days_ago)).replace(hour=10 + self.counter % 8, minute=0, second=0, microsecond=0)
        (self.folder / "Транскрипты").mkdir(parents=True, exist_ok=True)
        source = Path(f"PLAUD_dos9_{self.counter}_{days_ago}.mp3")
        stem = f"{recorded:%Y-%m-%d %H-%M} — {title}"
        transcript_name = f"{stem} — стенограмма.md"
        data = {"title": title, "summary": list(summary), "topics": [], "proposals": [],
                "decisions": [{"text": t, "evidence_time": "00:02:00"} for t in decisions],
                "tasks": [{"task": t, "owner": "Анна", "deadline": "не указан", "evidence_time": "00:01:30"} for t in tasks],
                "risks": [{"text": t, "evidence_time": "00:03:10"} for t in risks],
                "open_questions": [{"text": q, "evidence_time": f"00:0{i + 1}:05"} for i, q in enumerate(questions)], "next_steps": []}
        note = mp.render_note(source, recorded, 300, data, transcript_name)
        if project:
            note = note.replace('project_id: ""', 'project_id: "kxd"\nproject: "КХД"')
        path = self.folder / f"{stem}.md"
        path.write_text(note, encoding="utf-8")
        (self.folder / "Транскрипты" / transcript_name).write_text(
            mp.render_transcript(source, recorded, 300, title, path.name, "[00:00:01] **Спикер 1:** Начнём.\n"), encoding="utf-8")
        self.core.reload(force=True)
        meeting = next(m for m in self.core.meetings if m.title == title)
        storage.set_meeting_people(meeting.id, list(people))
        self.made.append(meeting.id)
        self.core.data_version += 1
        return meeting.id

    def person(self, name=PERSON):
        return next(p for p in self.client.get("/api/people").json()["items"] if p["name"] == name)["id"]

    def dossier(self, name=PERSON):
        return self.client.get(f"/api/people/{self.person(name)}").json()

    def remove(self):
        shutil.rmtree(self.folder, ignore_errors=True)
        self.core.reload(force=True)
        with storage.connect() as db:
            for table in ("dossier_pins", "dossier_mutes", "dossier_gists"):
                db.execute(f"DELETE FROM {table}")


@pytest.fixture()
def notes(client, tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: (_ for _ in ()).throw(OSError("модели нет")))
    storage.initialize()
    made = Notes(client)
    yield made
    made.remove()


def five(notes):
    """Пять записей от старой к новой; Анна Петрова участвует во всех."""
    ids = [
        notes.add("Планёрка 1", 20, questions=[Q_HANG, Q_OLD, Q_ANSWERED], risks=["Сроки сдвигаются"], tasks=["Собрать требования"]),
        notes.add("Планёрка 2", 15, questions=[Q_OLD]),
        notes.add("Планёрка 3", 10, questions=[Q_HANG_2], decisions=["Выгрузку ведём в КХД"]),
        notes.add("Планёрка 4", 5, questions=[Q_ANSWERED]),
        notes.add("Планёрка 5", 1, questions=[Q_HANG, Q_SINGLE], decisions=["Поставщика для лицензий выбираем по цене"]),
    ]
    return ids


def texts(items):
    return [i["text"] for i in items]


# --------------------------------------------------------------------------- висящие вопросы

def test_hanging_requires_two_meetings_and_recent_mention(client, notes):
    ids = five(notes)
    hanging = notes.dossier()["hanging"]
    found = {q["text"]: q for q in hanging["items"]}
    assert Q_HANG in found or Q_HANG_2 in found
    q = found.get(Q_HANG) or found.get(Q_HANG_2)
    assert q["count"] == 3 and q["first"] != q["last"] and len(q["occurrences"]) == 3
    assert Q_SINGLE not in found                      # прозвучал один раз
    assert Q_OLD not in found                         # давно не поднимался
    assert Q_ANSWERED not in found                    # позже принято решение по теме
    assert hanging["from"] == 2 and hanging["total"] == len(hanging["items"])
    # у каждого упоминания есть запись и таймкод
    for occurrence in q["occurrences"]:
        assert occurrence["meetingId"] in ids and occurrence["sec"] is not None and occurrence["meetingTitle"].startswith("Планёрка")
    assert [o["meetingId"] for o in q["occurrences"]] == [ids[0], ids[2], ids[4]]
    # самая свежая формулировка
    assert q["text"] == Q_HANG


def test_two_meetings_are_enough_and_one_is_not(client, notes):
    notes.add("Первая", 6, questions=[Q_HANG])
    assert notes.dossier()["hanging"]["items"] == []
    notes.add("Вторая", 2, questions=[Q_HANG_2])
    items = notes.dossier()["hanging"]["items"]
    assert len(items) == 1 and items[0]["count"] == 2


def test_same_question_twice_in_one_record_counts_once(client, notes):
    notes.add("Одна запись", 3, questions=[Q_HANG, Q_HANG_2])
    assert notes.dossier()["hanging"]["items"] == []


def test_hanging_in_project_dossier_and_health_reason(client, notes):
    five(notes)
    data = client.get("/api/projects/kxd").json()
    assert any(Q_HANG in q["text"] for q in data["hanging"]["items"])
    assert any(r.startswith("висящих вопросов") for r in data["health"]["reasons"])


def test_mute_hides_question_and_can_be_undone(client, notes):
    ids = five(notes)
    pid = notes.person()
    before = notes.dossier()["hanging"]
    assert before["items"]
    r = client.post(f"/api/people/{pid}/hanging/mute", json={"text": Q_HANG_2, "meetingId": ids[4]})
    assert r.status_code == 200, r.text
    mute_id = r.json()["id"]
    after = notes.dossier()["hanging"]
    assert after["items"] == [] and [m["text"] for m in after["muted"]] == [Q_HANG_2]
    # повтор по сути того же вопроса не плодит строк
    assert client.post(f"/api/people/{pid}/hanging/mute", json={"text": Q_HANG}).json()["id"] == mute_id
    # новая запись с тем же вопросом его не возвращает
    notes.add("Планёрка 6", 0, questions=[Q_HANG])
    assert notes.dossier()["hanging"]["items"] == []
    assert client.delete(f"/api/hanging/{mute_id}").status_code == 200
    assert notes.dossier()["hanging"]["items"] and notes.dossier()["hanging"]["muted"] == []
    assert client.delete(f"/api/hanging/{mute_id}").status_code == 404
    assert client.post("/api/people/нет/hanging/mute", json={"text": "x"}).status_code == 404
    assert client.post(f"/api/people/{pid}/hanging/mute", json={"text": "  "}).status_code == 400


# --------------------------------------------------------------------------- закреплённые факты

def test_pin_from_fact_keeps_source_and_survives_refresh(client, notes):
    ids = five(notes)
    pid = notes.person()
    r = client.post(f"/api/people/{pid}/pins", json={"text": Q_HANG, "kind": "question", "meetingId": ids[4], "sec": 65})
    assert r.status_code == 200, r.text
    pin = r.json()
    assert pin["kind"] == "question" and pin["kindLabel"] == "Вопрос" and pin["edited"] is False
    assert pin["source"]["meetingId"] == ids[4] and pin["source"]["sec"] == 65 and pin["source"]["meetingTitle"] == "Планёрка 5"
    # дубль возвращает ту же закладку
    assert client.post(f"/api/people/{pid}/pins", json={"text": Q_HANG.upper(), "kind": "question", "meetingId": ids[4]}).json()["id"] == pin["id"]
    assert [p["id"] for p in notes.dossier()["pins"]] == [pin["id"]]
    # правка пользователя помечается и не затирается пересчётом досье
    edited = client.patch(f"/api/pins/{pin['id']}", json={"text": "Выгрузку ведёт Анна — подтвердить у Ивана"}).json()
    assert edited["edited"] is True and edited["origin"] == Q_HANG
    notes.add("Планёрка 6", 0, questions=["Что с доступами"], summary=("Новая тема: доступы",))
    live.refresh_gist("person", pid, live.meetings_of(notes.core, "person", pid)[0], PERSON, use_model=False)
    notes.core.reload(force=True)
    again = notes.dossier()["pins"]
    assert len(again) == 1 and again[0]["text"] == "Выгрузку ведёт Анна — подтвердить у Ивана" and again[0]["edited"] is True
    assert again[0]["source"]["meetingId"] == ids[4]


def test_manual_pin_has_no_source(client, notes):
    five(notes)
    pid = notes.person()
    pin = client.post(f"/api/people/{pid}/pins", json={"text": "Предпочитает короткие письма", "meetingId": "", "kind": "risk", "sec": 12}).json()
    assert pin["source"] is None and pin["kind"] == "manual" and pin["kindLabel"] == "Моя заметка"
    project_pin = client.post("/api/projects/kxd/pins", json={"text": "Заказчик — ДЦТ"}).json()
    assert project_pin["source"] is None
    assert [p["text"] for p in notes.dossier()["pins"]] == ["Предпочитает короткие письма"]      # у проекта — свои
    assert [p["text"] for p in client.get("/api/projects/kxd").json()["pins"]] == ["Заказчик — ДЦТ"]
    assert client.delete(f"/api/pins/{pin['id']}").status_code == 200
    assert notes.dossier()["pins"] == []
    assert client.delete(f"/api/pins/{pin['id']}").status_code == 404
    assert client.patch(f"/api/pins/{pin['id']}", json={"text": "x"}).status_code == 404


def test_pin_validation(client, notes):
    ids = five(notes)
    pid = notes.person()
    for bad in ("", "   "):
        assert client.post(f"/api/people/{pid}/pins", json={"text": bad}).status_code == 400
    assert client.post(f"/api/people/{pid}/pins", json={"text": "x", "meetingId": "нет-такой"}).status_code == 400
    assert client.post("/api/people/нет/pins", json={"text": "x"}).status_code == 404
    assert client.post("/api/projects/нет/pins", json={"text": "x"}).status_code == 404
    assert client.post(f"/api/people/{pid}/pins", json={"text": "x" * 2001}).status_code == 422
    long = client.post(f"/api/people/{pid}/pins", json={"text": "я" * 1500}).json()
    assert len(long["text"]) == live.PIN_TEXT
    pin = client.post(f"/api/people/{pid}/pins", json={"text": "Факт", "meetingId": ids[0], "sec": -5}).json()
    assert pin["source"]["sec"] == 0
    assert client.patch(f"/api/pins/{pin['id']}", json={"text": "  "}).status_code == 400


def test_pin_limit(client, notes):
    five(notes)
    pid = notes.person()
    for i in range(live.PIN_LIMIT):
        assert client.post(f"/api/people/{pid}/pins", json={"text": f"Заметка {i}"}).status_code == 200
    r = client.post(f"/api/people/{pid}/pins", json={"text": "Лишняя"})
    assert r.status_code == 400 and str(live.PIN_LIMIT) in r.json()["detail"]


def test_pin_outlives_source_note(client, notes):
    ids = five(notes)
    pid = notes.person()
    pin = client.post(f"/api/people/{pid}/pins", json={"text": "Факт из пятой записи", "meetingId": ids[4], "sec": 10, "kind": "summary"}).json()
    (path,) = [p for p in notes.folder.glob("*Планёрка 5.md")]
    path.unlink()
    notes.core.reload(force=True)
    found = notes.dossier()["pins"][0]
    assert found["id"] == pin["id"] and found["text"] == "Факт из пятой записи" and found["source"] is None and found["sourceGone"] is True


# --------------------------------------------------------------------------- о чём разговор

def test_gist_without_model_is_structural_and_linked(client, notes):
    ids = five(notes)
    gist = notes.dossier()["gist"]
    assert gist["engine"] == "structure" and gist["stale"] is False and "выгрузку справочника" in gist["text"]
    assert [s["meetingId"] for s in gist["sources"]] == ids[::-1][:live.GIST_MEETINGS]
    assert gist["updatedAt"] == "" and gist["busy"] is False
    assert len(gist["text"]) <= live.GIST_MAX


def test_gist_empty_when_no_records(client, notes):
    pid = client.post("/api/people", json={"name": "Совсем Новый"}).json()["id"]
    data = client.get(f"/api/people/{pid}").json()
    assert data["gist"]["text"] == "" and data["hanging"]["items"] == [] and data["pins"] == [] and data["changes"] == []


def test_view_never_calls_model(client, notes, monkeypatch):
    five(notes)
    calls = []
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: calls.append(prompt) or "Разговор о выгрузке справочника.")
    notes.dossier()
    client.get("/api/projects/kxd")
    assert calls == []


def test_model_gist_is_stored_and_served_until_records_change(client, notes, monkeypatch):
    five(notes)
    pid = notes.person()
    answer = "Обсуждают выгрузку справочника и ответственных за неё."
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: answer)
    meetings, name = live.meetings_of(notes.core, "person", pid)
    assert name == PERSON
    assert live.refresh_gist("person", pid, meetings, name)["engine"] == "model"
    gist = notes.dossier()["gist"]
    assert gist["text"] == answer and gist["engine"] == "model" and gist["stale"] is False and gist["updatedAt"]
    # появилась новая запись: старый абзац не выдаётся за актуальный
    notes.add("Планёрка 6", 0, summary=("Совсем другая тема: доступы подрядчиков",))
    stale = notes.dossier()["gist"]
    assert stale["stale"] is True and stale["engine"] == "structure" and "доступы подрядчиков" in stale["text"]


@pytest.mark.parametrize("bad", [
    "Обсуждают выгрузку и 15 задач.",                                   # новая цифра
    "Обсуждают выгрузку справочника с Кириллом.",                      # имя, которого нет в пунктах
    "Первая строка.\nВторая строка про выгрузку.",                     # переносы
    "",                                                                # пусто
    "Обсуждают выгрузку справочника. " * 30,                           # слишком длинно
])
def test_untrusted_model_answer_falls_back(client, notes, monkeypatch, bad):
    five(notes)
    pid = notes.person()
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: bad)
    meetings, name = live.meetings_of(notes.core, "person", pid)
    saved = live.refresh_gist("person", pid, meetings, name)
    assert saved["engine"] == "structure" and saved["text"]


def test_model_may_use_names_present_in_source(client, notes, monkeypatch):
    notes.add("Запись", 2, summary=("Анна просит выгрузку справочника",))
    pid = notes.person()
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: "Речь о том, что Анна просит выгрузку справочника.")
    meetings, name = live.meetings_of(notes.core, "person", pid)
    assert live.refresh_gist("person", pid, meetings, name)["engine"] == "model"


def test_prompt_contains_only_summaries(client, notes, monkeypatch):
    notes.add("Запись", 2, summary=("Пункт про выгрузку",), questions=["Секретный вопрос"], risks=["Секретный риск"])
    seen = []
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: seen.append(prompt) or "")
    pid = notes.person()
    meetings, name = live.meetings_of(notes.core, "person", pid)
    live.refresh_gist("person", pid, meetings, name)
    assert len(seen) == 1 and "Пункт про выгрузку" in seen[0] and "Секретн" not in seen[0]


# --------------------------------------------------------------------------- обновление по событию

def wait_idle(core, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        with live._state_lock:
            if not any(v.get("busy") for v in live._state.values()):
                return True
        time.sleep(0.02)
    return False


def test_record_processed_refreshes_people_and_project(client, notes, monkeypatch):
    five(notes)
    new_id = notes.add("Планёрка 6", 0, summary=("Подключаем стенд для проверок",))
    owners = live.owners_of_meeting(notes.core, new_id)
    assert ("person", notes.person()) in owners and ("person", notes.person(OTHER)) in owners and ("project", "kxd") in owners
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: "Обсуждают подключение стенда для проверок.")
    before = notes.core.data_version
    assert live.refresh_for_meeting_async(notes.core, new_id) is True
    assert wait_idle(notes.core)
    gist = notes.dossier()["gist"]
    assert gist["engine"] == "model" and gist["text"].startswith("Обсуждают подключение стенда")
    assert client.get("/api/projects/kxd").json()["gist"]["engine"] == "model"
    assert notes.core.data_version > before
    rows = [r for r in audit.read_all() if r["action"] == "dossier.refresh"]
    assert rows and rows[-1]["actor"] == "system" and rows[-1]["detail"].startswith("досье: ")


def test_refresh_without_model_setting_stores_structural(client, notes, monkeypatch):
    five(notes)
    calls = []
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: calls.append(1) or "Что-то")
    new_id = notes.add("Планёрка 6", 0)
    assert live.refresh_for_meeting_async(notes.core, new_id, use_model=False)
    assert wait_idle(notes.core) and calls == []
    assert notes.dossier()["gist"]["engine"] == "structure"


def test_refresh_endpoint_and_busy_flag(client, notes, monkeypatch):
    five(notes)
    pid = notes.person()
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: "Обсуждают выгрузку справочника.")
    r = client.post(f"/api/people/{pid}/refresh")
    assert r.status_code == 200 and r.json()["started"] in (True, False)
    assert wait_idle(notes.core)
    assert notes.dossier()["gist"]["engine"] == "model"
    assert client.post("/api/projects/kxd/refresh").status_code == 200 and wait_idle(notes.core)
    assert client.post("/api/people/нет/refresh").status_code == 404
    assert client.post("/api/projects/нет/refresh").status_code == 404


def test_second_refresh_is_skipped_while_running(client, notes):
    five(notes)
    key = ("person", notes.person())
    with live._state_lock:
        live._state[key] = {"busy": True}
    try:
        assert live.refresh_async(notes.core, [key]) is False
        assert notes.dossier()["gist"]["busy"] is True
    finally:
        with live._state_lock:
            live._state.pop(key, None)


# --------------------------------------------------------------------------- что изменилось

def test_changes_show_what_each_record_brought(client, notes):
    five(notes)
    changes = notes.dossier()["changes"]
    assert [c["meetingTitle"] for c in changes] == ["Планёрка 5", "Планёрка 4", "Планёрка 3", "Планёрка 2", "Планёрка 1"]
    assert changes[0]["decisions"] == 1 and changes[0]["questions"] == 2 and changes[0]["tasks"] == 0
    assert changes[-1]["tasks"] == 1 and changes[-1]["risks"] == 1 and changes[-1]["questions"] == 3
    notes.add("Планёрка 6", 0, decisions=["Новое решение", "Ещё решение"])
    fresh = notes.dossier()["changes"]
    assert len(fresh) == live.CHANGES_LIMIT and fresh[0]["meetingTitle"] == "Планёрка 6" and fresh[0]["decisions"] == 2


def test_new_record_changes_at_least_one_section(client, notes):
    five(notes)
    before = notes.dossier()
    notes.add("Планёрка 6", 0, questions=[Q_HANG_2], risks=["Новый риск"], summary=("Свежая тема про бюджет",))
    after = notes.dossier()
    assert after["stats"]["meetings"] == before["stats"]["meetings"] + 1
    assert after["changes"] != before["changes"] and after["gist"]["text"] != before["gist"]["text"]
    assert after["hanging"]["items"][0]["count"] == before["hanging"]["items"][0]["count"] + 1


# --------------------------------------------------------------------------- стирание, журнал, настройки

def test_erase_takes_dossier_traces_with_the_record(client, notes, monkeypatch):
    ids = five(notes)
    pid = notes.person()
    monkeypatch.setattr(digest, "generate", lambda prompt, n=700: "Обсуждают выгрузку справочника.")
    meetings, name = live.meetings_of(notes.core, "person", pid)
    live.refresh_gist("person", pid, meetings, name)
    mine = client.post(f"/api/people/{pid}/pins", json={"text": "Моя личная заметка"}).json()
    sourced = client.post(f"/api/people/{pid}/pins", json={"text": "Из пятой записи", "meetingId": ids[4], "sec": 5}).json()
    other = client.post(f"/api/people/{pid}/pins", json={"text": "Из первой записи", "meetingId": ids[0], "sec": 5}).json()
    client.post(f"/api/people/{pid}/hanging/mute", json={"text": "Закрытый вопрос из пятой", "meetingId": ids[4]})
    plan = client.get(f"/api/meetings/{ids[4]}/erase/plan").json()
    registry = next(i for i in plan["items"] if i["key"] == "registry")
    assert "строк досье" in registry["detail"]
    r = client.post(f"/api/meetings/{ids[4]}/erase", json={"confirm": "СТЕРЕТЬ", "scrubBackups": False, "noteCopies": False})
    assert r.status_code == 200, r.text
    with storage.connect() as db:
        pins = {row[0] for row in db.execute("SELECT id FROM dossier_pins")}
        assert pins == {mine["id"], other["id"]} and sourced["id"] not in pins
        assert db.execute("SELECT COUNT(*) FROM dossier_mutes").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM dossier_gists").fetchone()[0] == 0     # абзац строился и по стёртой записи
    data = notes.dossier()
    assert {p["id"] for p in data["pins"]} == {mine["id"], other["id"]}


def test_erase_removes_dossier_rows_from_backups(client, notes, tmp_path):
    import sqlite3
    ids = five(notes)
    pid = notes.person()
    client.post(f"/api/people/{pid}/pins", json={"text": "Из пятой записи", "meetingId": ids[4]})
    copy = tmp_path / "copy.db"
    with storage.connect() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(storage.db_path(), copy)
    assert erase._scrub_backup(copy, ids[4]) is True
    db = sqlite3.connect(copy)
    try:
        assert db.execute("SELECT COUNT(*) FROM dossier_pins WHERE meeting_id=?", (ids[4],)).fetchone()[0] == 0
    finally:
        db.close()


def test_every_new_route_is_audited(client, notes):
    ids = five(notes)
    pid = notes.person()
    pin = client.post(f"/api/people/{pid}/pins", json={"text": "Секретный текст закладки", "meetingId": ids[0]}).json()
    client.patch(f"/api/pins/{pin['id']}", json={"text": "Другой секретный текст"})
    mute = client.post("/api/projects/kxd/hanging/mute", json={"text": "Секретный вопрос проекта"}).json()
    client.delete(f"/api/hanging/{mute['id']}")
    client.delete(f"/api/pins/{pin['id']}")
    rows = audit.read_all()
    actions = [r["action"] for r in rows]
    for code in ("dossier.pin", "dossier.pin_edit", "dossier.mute", "dossier.unmute", "dossier.unpin"):
        assert code in actions, code
    assert not any("секретн" in str(r).casefold() for r in rows)          # в журнале только идентификаторы
    listed = client.get("/api/audit", params={"limit": 50}).json()["items"]
    assert any(i["action"] == "dossier.pin" and i["object"] == PERSON for i in listed)
    assert any(i["action"] == "dossier.unpin" and "закреплённый факт" in i["object"] for i in listed)


def test_dossier_model_setting(client):
    original = client.get("/api/settings").json()
    body = {k: original[k] for k in ("recordings_dir", "obsidian_dir", "archive_dir")}
    assert original["dossier_model"] is True
    r = client.put("/api/settings", json={**body, "dossier_model": False})
    assert r.status_code == 200 and r.json()["dossier_model"] is False
    # запрос без поля не сбрасывает выбор
    assert client.put("/api/settings", json=body).json()["dossier_model"] is False
    assert client.put("/api/settings", json={**body, "dossier_model": True}).json()["dossier_model"] is True
    client.put("/api/settings", json={k: v for k, v in original.items() if k != "exists"})     # вернуть прежние настройки
