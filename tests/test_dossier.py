"""Карточки людей (файлы в Obsidian), досье человека и проекта."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import memory_desk.knowledge, memory_desk.people, memory_desk.storage  # noqa: F401
    globals().update(knowledge=memory_desk.knowledge, people=memory_desk.people, storage=memory_desk.storage)


def card_file(name):
    return people.card_path(name)


def by_name(client, name):
    return next(p for p in client.get("/api/people").json()["items"] if p["name"] == name)


# --------------------------------------------------------------------------- карточки

def test_create_person_writes_card_and_row(client):
    r = client.post("/api/people", json={"name": "Ирина Белова", "role": "Руководитель группы", "organization": "ДЦТ",
                                         "aliases": "Ира, Ирина Б.", "notes": "Любит короткие письма.\n## Не раздел"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and body["role"] == "Руководитель группы" and body["organization"] == "ДЦТ"
    assert body["aliases"] == ["Ира", "Ирина Б."]
    text = card_file("Ирина Белова").read_text(encoding="utf-8")
    assert text.startswith("---\ntype: person\n") and 'role: "Руководитель группы"' in text
    assert "## Заметки" in text and "### Не раздел" in text and "\n## Не раздел" not in text
    person = by_name(client, "Ирина Белова")
    assert person["organization"] == "ДЦТ" and person["notes"].startswith("Любит короткие письма")


def test_create_again_updates_and_existing_participant_gets_card(client):
    first = client.post("/api/people", json={"name": "Игорь Тестов", "role": "Аналитик"}).json()
    again = client.post("/api/people", json={"name": "игорь   тестов", "role": "Старший аналитик"}).json()
    assert again["id"] == first["id"] and again["created"] is False and again["role"] == "Старший аналитик"
    assert len([p for p in client.get("/api/people").json()["items"] if p["name"].casefold() == "игорь тестов"]) == 1
    # человек, уже встречающийся в записях (Анна из тестовых данных), получает карточку без дубля
    anna = by_name(client, "Анна Соколова")
    r = client.post("/api/people", json={"name": "Анна Соколова", "role": "Ведущий аналитик"}).json()
    assert r["id"] == anna["id"] and r["created"] is False
    assert by_name(client, "Анна Соколова")["meetings"] == anna["meetings"]


def test_validation(client):
    for bad in ("", "   ", "А/Б", "a:b", "x" * 121, "***"):
        assert client.post("/api/people", json={"name": bad}).status_code == 400, bad
    assert client.post("/api/people", json={}).status_code == 422
    assert client.patch("/api/people/нет-такого", json={"role": "x"}).status_code == 404
    pid = by_name(client, "Ирина Белова")["id"]
    assert client.patch(f"/api/people/{pid}", json={"имя": "x"}).status_code == 400


def test_patch_keeps_foreign_content_of_the_card(client):
    client.post("/api/people", json={"name": "Олег Карточкин", "role": "Аналитик", "notes": "Первая заметка"})
    path = card_file("Олег Карточкин")
    text = path.read_text(encoding="utf-8")
    # человек дописал в Obsidian свои поля и раздел
    text = text.replace("type: person\n", "type: person\ncustom_field: свой\n", 1) + "\n## Контакты\n\nТелефон в секретариате\n"
    path.write_text(text, encoding="utf-8")
    pid = by_name(client, "Олег Карточкин")["id"]
    assert client.patch(f"/api/people/{pid}", json={"role": "Руководитель аналитики", "aliases": ["Олежа"]}).status_code == 200
    after = path.read_text(encoding="utf-8")
    assert "custom_field: свой" in after and "## Контакты\n\nТелефон в секретариате" in after
    assert 'role: "Руководитель аналитики"' in after and "Первая заметка" in after
    assert after.count("---\n") == 2 and after.count("## Заметки") == 1
    assert by_name(client, "Олег Карточкин")["aliases"] == ["Олежа"]
    # пустое значение очищает поле
    client.patch(f"/api/people/{pid}", json={"organization": "", "notes": ""})
    assert "organization" not in path.read_text(encoding="utf-8")


def test_card_survives_database_loss(client):
    client.post("/api/people", json={"name": "Мария Устойчивая", "role": "Архитектор", "organization": "ИТ", "aliases": ["Маша"]})
    storage.rows("SELECT 1")
    with storage.connect() as db:
        db.execute("DELETE FROM people WHERE name=?", ("Мария Устойчивая",))
    assert not any(p["name"] == "Мария Устойчивая" for p in client.get("/api/people").json()["items"])
    from memory_desk.app import create_app  # noqa: F401
    storage.sync_person_cards(people.load_cards())
    restored = by_name(client, "Мария Устойчивая")
    assert restored["role"] == "Архитектор" and restored["organization"] == "ИТ" and restored["aliases"] == ["Маша"]


def test_foreign_file_is_not_overwritten(client):
    path = card_file("Чужой Файл")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: note\n---\n\n# Не карточка\n", encoding="utf-8")
    assert client.post("/api/people", json={"name": "Чужой Файл"}).status_code == 400
    assert "Не карточка" in path.read_text(encoding="utf-8")


def test_alias_links_tasks_to_person(client):
    me = client.post("/api/people", json={"name": "Пётр Алиасов", "aliases": ["Петрович"]}).json()
    with storage.connect() as db:
        meeting = db.execute("SELECT id FROM meetings LIMIT 1").fetchone()["id"]
        db.execute("INSERT OR REPLACE INTO tasks (id,meeting_id,text,owner,status,updated_at) VALUES (?,?,?,?,?,?)",
                   ("alias-task-1", meeting, "Задача для Петровича", "Петрович", "open", storage.now_iso()))
    assert by_name(client, "Пётр Алиасов")["obligations"] == 1
    with storage.connect() as db:
        db.execute("DELETE FROM tasks WHERE id='alias-task-1'")


# --------------------------------------------------------------------------- досье человека

def test_person_dossier(client):
    anna = by_name(client, "Анна Соколова")
    r = client.get(f"/api/people/{anna['id']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["person"]["name"] == "Анна Соколова"
    st = d["stats"]
    assert st["meetings"] == anna["meetings"] >= 2
    assert st["lastContact"] and st["daysSince"] is not None and st["daysSince"] >= 0
    assert st["firstContact"] <= st["lastContact"]
    assert st["lastMeeting"]["id"]
    assert d["tasks"]["open"] == len(d["tasks"]["items"]) or d["tasks"]["open"] > 5
    assert {"open", "overdue", "stuck", "done", "drafts"} <= set(d["tasks"])
    assert d["projects"] and d["projects"][0]["title"] == "КХД" and 0 < d["projects"][0]["share"] <= 1
    assert d["timeline"] and d["timeline"][0]["date"] >= d["timeline"][-1]["date"]
    assert any(c["name"] == "Михаил Орлов" for c in d["companions"])
    assert all(x["meetingId"] for x in d["decisions"]["items"])
    if st["talk"]:
        assert 0 <= st["talk"]["share"] <= 1


def test_person_without_records_has_empty_but_valid_dossier(client):
    made = client.post("/api/people", json={"name": "Новичок Пустой"}).json()
    d = client.get(f"/api/people/{made['id']}").json()
    assert d["stats"]["meetings"] == 0 and d["stats"]["lastContact"] is None and d["stats"]["daysSince"] is None
    assert d["stats"]["talk"] is None and d["tasks"]["open"] == 0 and d["projects"] == [] and d["timeline"] == []


def test_person_dossier_404(client):
    assert client.get("/api/people/нет-такого").status_code == 404
    assert client.get("/api/projects/нет-такого").status_code == 404


def test_person_tasks_counted_by_alias_and_overdue(client):
    me = client.post("/api/people", json={"name": "Семён Сроков", "aliases": ["Сёма"]}).json()
    with storage.connect() as db:
        meeting = db.execute("SELECT id FROM meetings WHERE archived=0 LIMIT 1").fetchone()["id"]
        db.execute("INSERT OR REPLACE INTO tasks (id,meeting_id,text,owner,deadline,status,updated_at) VALUES (?,?,?,?,?,?,?)",
                   ("dos-1", meeting, "Просроченная задача", "Сёма", "2020-01-01", "open", storage.now_iso()))
        db.execute("INSERT OR REPLACE INTO tasks (id,meeting_id,text,owner,deadline,status,updated_at) VALUES (?,?,?,?,?,?,?)",
                   ("dos-2", meeting, "Сделанная задача", "Семён Сроков", "", "done", storage.now_iso()))
    try:
        t = client.get(f"/api/people/{me['id']}").json()["tasks"]
        assert t["open"] == 1 and t["overdue"] == 1 and t["done"] == 1
        assert t["items"][0]["text"] == "Просроченная задача"
    finally:
        with storage.connect() as db:
            db.execute("DELETE FROM tasks WHERE id IN ('dos-1','dos-2')")


# --------------------------------------------------------------------------- досье проекта

def test_project_dossier(client):
    d = client.get("/api/projects/kxd").json()
    assert d["project"]["title"] == "КХД" and d["project"]["status"] == "active"
    assert d["counts"]["records"] >= 3 and d["counts"]["first"] <= d["counts"]["last"]
    h = d["health"]
    assert h["level"] in {"ok", "warn", "bad"} and h["openTasks"] >= 1
    assert {p["name"] for p in d["people"]} >= {"Анна Соколова", "Михаил Орлов"}
    assert all(p["meetings"] >= 1 for p in d["people"])
    assert d["tasksByPerson"] and d["tasksByPerson"][0]["count"] >= d["tasksByPerson"][-1]["count"]
    assert len(d["weeks"]) == 12 and sum(w["meetings"] for w in d["weeks"]) <= d["counts"]["records"]
    assert d["recent"] and d["decisions"]["activeTotal"] >= 1
    assert isinstance(d["risks"], list) and isinstance(d["questions"], list)


def test_project_health_levels(client):
    import memory_desk.dossier as dossier
    from datetime import date
    # «сегодня» через несколько лет: проект давно без встреч, задачи просрочены
    far = dossier.project(client.app.state.core, "kxd", today=date(2030, 1, 1))
    assert far["health"]["level"] in {"warn", "bad"} and any("встреч не было" in r for r in far["health"]["reasons"])
    assert far["health"]["overdue"] >= 1


def test_project_patch_status_and_goal_roundtrip(client):
    path = next(p["path"] for p in knowledge.load_projects() if p["id"] == "kxd")
    before = Path(path).read_text(encoding="utf-8")
    try:
        r = client.patch("/api/projects/kxd", json={"status": "paused", "goal": "Новая цель проекта\n## не раздел"})
        assert r.status_code == 200, r.text
        d = client.get("/api/projects/kxd").json()["project"]
        assert d["status"] == "paused" and d["statusLabel"] == "На паузе" and d["goal"].startswith("Новая цель проекта")
        text = Path(path).read_text(encoding="utf-8")
        assert "status: paused" in text and text.count("## Цели") == 1 and "### не раздел" in text
        assert "## О проекте" in text                                   # остальное не тронуто
        assert [p for p in client.get("/api/projects").json()["items"] if p["id"] == "kxd"][0]["status"] == "paused"
    finally:
        Path(path).write_text(before, encoding="utf-8")
        client.patch("/api/projects/kxd", json={"status": "active"})


def test_project_patch_validation(client):
    assert client.patch("/api/projects/kxd", json={}).status_code == 400
    assert client.patch("/api/projects/kxd", json={"status": "сломан"}).status_code == 400
    assert client.patch("/api/projects/нет", json={"status": "done"}).status_code == 404
