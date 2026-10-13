"""Этап 4.3: служебная записка, бриф и письмо участникам — у каждого пункта проверенный таймкод."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _modules(fixture_dir, monkeypatch):
    sys.path.insert(0, str(ROOT))
    import memory_desk.artifacts, memory_desk.digest, memory_desk.knowledge, memory_desk.materials, memory_desk.storage  # noqa: F401
    globals().update(artifacts=memory_desk.artifacts, digest=memory_desk.digest, knowledge=memory_desk.knowledge,
                     materials=memory_desk.materials, storage=memory_desk.storage)
    # по умолчанию модель недоступна: материал обязан собираться структурно
    monkeypatch.setattr(digest, "generate", lambda *a, **k: (_ for _ in ()).throw(OSError("нет связи")))


def rich_meeting(client):
    """Запись с решениями, задачами и вопросами."""
    for m in client.get("/api/meetings", params={"limit": 1000}).json()["items"]:
        d = client.get(f"/api/meetings/{m['id']}").json()
        if d["decisions"] and d["tasks"]:
            return d
    raise AssertionError("в тестовых данных нет записи с решениями и задачами")


def person_id(client, name="Анна Соколова"):
    return next(p["id"] for p in client.get("/api/people").json()["items"] if p["name"] == name)


def project_with_records(client):
    return next(p for p in client.get("/api/projects").json()["items"] if p["counts"]["records"] >= 2)


def make(client, kind, **scope):
    r = client.post("/api/materials/artifact", json={"kind": kind, **scope})
    assert r.status_code == 200, r.text
    return r.json()


def stamps(markdown):
    return re.findall(r"⏱ (\d{2}:\d{2}:\d{2})", markdown)


def sec(stamp):
    h, m, s = map(int, stamp.split(":"))
    return h * 3600 + m * 60 + s


KINDS = ("memo", "brief", "followup")


# --------------------------------------------------------------------------- таймкод у каждого пункта

@pytest.mark.parametrize("kind", KINDS)
def test_record_every_item_has_valid_timecode(client, kind):
    d = rich_meeting(client)
    out = make(client, kind, meetingId=d["id"])
    assert out["mode"] == "structured" and out["count"] > 0
    assert artifacts.untimed(out["markdown"]) == []
    found = stamps(out["markdown"])
    assert len(found) >= 3
    # каждый таймкод — внутри записи
    limit = d["durationSec"]
    assert all(sec(t) <= limit + 5 for t in found), (found, limit)


@pytest.mark.parametrize("kind", KINDS)
def test_project_and_person_scopes_have_timecodes_and_record_names(client, kind):
    project = project_with_records(client)
    for scope in ({"projectId": project["id"], "days": 3650}, {"personId": person_id(client), "days": 3650}):
        out = make(client, kind, **scope)
        assert out["records"] >= 2
        assert artifacts.untimed(out["markdown"]) == []
        titles = {r["label"] for r in out["refs"]}
        # у пункта из нескольких записей названа запись, из которой он взят
        item_lines = [l for l in out["markdown"].splitlines() if "⏱" in l]
        assert item_lines and all(any(t in l for t in titles) for l in item_lines)
        if kind == "followup":
            assert "[[" not in out["markdown"]          # в письме нет ссылок Obsidian
        else:
            assert "[[" in out["markdown"]


def test_timecodes_match_the_record_they_name(client):
    """Таймкод не выходит за длительность той записи, на которую ссылается строка."""
    project = project_with_records(client)
    out = make(client, "memo", projectId=project["id"], days=3650)
    durations = {r["label"]: next(m for m in client.get("/api/meetings", params={"limit": 1000}).json()["items"] if m["id"] == r["id"]) for r in out["refs"]}
    for line in out["markdown"].splitlines():
        match = re.search(r"⏱ (\d{2}:\d{2}:\d{2})", line)
        if not match:
            continue
        title = next(t for t in durations if t in line)
        assert sec(match.group(1)) <= (durations[title]["durationSec"] or 10**9) + 5


def test_sections_follow_the_spec(client):
    d = rich_meeting(client)
    memo = make(client, "memo", meetingId=d["id"], )["markdown"]
    for head in ("**Кому:**", "## Суть", "## Обоснование", "## Решения", "## Что просим"):
        assert head in memo
    brief = make(client, "brief", meetingId=d["id"])["markdown"]
    for head in ("## Цель", "## Контекст", "## Договорённости", "## Сроки", "## Ответственные", "## Открытые вопросы"):
        assert head in brief
    letter = make(client, "followup", meetingId=d["id"])["markdown"]
    for head in ("**Тема:**", "**Кому:**", "## Что решили", "## Кто что делает"):
        assert head in letter
    assert "Черновик. Письмо не отправлено" in letter


def test_brief_goal_comes_from_project_file(client):
    project = project_with_records(client)
    brief = make(client, "brief", projectId=project["id"], days=3650)["markdown"]
    text = brief.split("## Цель", 1)[1].split("## Контекст", 1)[0]
    assert "[впишите цель]" not in text and len(text.strip()) > 10


def test_addressee_is_placed_and_placeholder_otherwise(client):
    d = rich_meeting(client)
    assert "**Кому:** [укажите адресата]" in make(client, "memo", meetingId=d["id"])["markdown"]
    out = client.post("/api/materials/artifact", json={"kind": "memo", "meetingId": d["id"], "addressee": "Директору по ИТ Петрову П. П."}).json()
    assert "**Кому:** Директору по ИТ Петрову П. П." in out["markdown"]


# --------------------------------------------------------------------------- проверка и поиск таймкода

def recording(monkeypatch, duration, lines):
    from types import SimpleNamespace
    segments = [{"i": n, "t": t, "sec": sec(t), "speaker": "", "text": text} for n, (t, text) in enumerate(lines)]
    monkeypatch.setattr(knowledge, "transcript_segments", lambda m: [dict(s) for s in segments])
    return artifacts._Recording(SimpleNamespace(duration=duration))


def test_timecode_validation_bounds(monkeypatch):
    rec = recording(monkeypatch, "00:10:00", [("00:00:10", "начали встречу"), ("00:09:30", "закончили")])
    assert rec.valid("00:05:00") == 300
    assert rec.valid("00:10:03") == 603          # допуск в несколько секунд
    assert rec.valid("00:10:30") is None         # после конца записи
    assert rec.valid("5:00") is None and rec.valid("") is None and rec.valid("abc") is None


def test_timecode_is_located_in_transcript(monkeypatch):
    rec = recording(monkeypatch, "00:30:00", [
        ("00:01:00", "Привет всем, начинаем"),
        ("00:12:30", "Нужно подготовить выгрузку справочника клиентов до пятницы"),
        ("00:20:00", "Обсудим бюджет на следующий квартал")])
    assert rec.locate("Подготовить выгрузку справочника клиентов") == sec("00:12:30")
    assert rec.locate("Согласовать лицензии на сервер аналитики") is None      # в разговоре этого не было
    assert rec.locate("") is None


def test_invalid_timecode_is_replaced_or_item_dropped(client, monkeypatch):
    """Таймкод в заметке за пределами записи не попадает в документ: берётся место из стенограммы или пункт исключается."""
    d = rich_meeting(client)
    core = client.app.state.core
    decision = d["decisions"][0]
    with storage.connect() as db:
        before = db.execute("SELECT evidence_time FROM decisions WHERE id=?", (decision["id"],)).fetchone()[0]
        db.execute("UPDATE decisions SET evidence_time='09:59:59' WHERE id=?", (decision["id"],))
    try:
        out = make(client, "memo", meetingId=d["id"])
        assert "09:59:59" not in out["markdown"]
        assert artifacts.untimed(out["markdown"]) == []
        text = decision["text"]
        if text in out["markdown"]:
            assert all(sec(t) <= d["durationSec"] + 5 for t in stamps(out["markdown"]))
        else:
            assert any(text[:30] in s["text"] for s in out["skipped"])
    finally:
        with storage.connect() as db:
            db.execute("UPDATE decisions SET evidence_time=? WHERE id=?", (before, decision["id"]))


def test_items_without_place_are_listed_as_skipped_and_noted(client):
    d = rich_meeting(client)
    out = make(client, "brief", meetingId=d["id"])
    for item in out["skipped"]:
        assert item["text"] not in out["markdown"]
        assert item["reason"] and item["meetingId"] == d["id"]
    if out["skipped"]:
        assert "Не включено пунктов без привязки к месту в записи" in out["markdown"]


# --------------------------------------------------------------------------- модель

def test_model_writes_only_the_lead(client, monkeypatch):
    d = rich_meeting(client)
    monkeypatch.setattr(digest, "generate", lambda *a, **k: "Обсудили доступ к тестовому контуру и распределили задачи.")
    for kind in KINDS:
        out = make(client, kind, meetingId=d["id"])
        assert out["mode"] == "model" and "Обсудили доступ к тестовому контуру и распределили задачи." in out["markdown"]
        assert artifacts.untimed(out["markdown"]) == []     # вводная — абзац, а пункты по-прежнему с таймкодами


def test_model_answer_with_new_numbers_is_rejected(client, monkeypatch):
    d = rich_meeting(client)
    monkeypatch.setattr(digest, "generate", lambda *a, **k: "Решили потратить 9 миллионов 777 тысяч рублей.")
    out = make(client, "memo", meetingId=d["id"])
    assert out["mode"] == "structured" and "777" not in out["markdown"]


def test_empty_or_multiline_model_answer_falls_back(client, monkeypatch):
    d = rich_meeting(client)
    for bad in ("", "Строка один\nСтрока два"):
        monkeypatch.setattr(digest, "generate", lambda *a, _bad=bad, **k: _bad)
        assert make(client, "followup", meetingId=d["id"])["mode"] == "structured"


# --------------------------------------------------------------------------- ошибки, сохранение, экспорт, чат

def test_errors(client):
    d = rich_meeting(client)
    assert client.post("/api/materials/artifact", json={"kind": "poem", "meetingId": d["id"]}).status_code == 400
    assert client.post("/api/materials/artifact", json={"kind": "memo", "meetingId": "нет-такой"}).status_code == 404
    assert client.post("/api/materials/artifact", json={"kind": "memo", "since": "2001-01-01", "until": "2001-01-31"}).status_code == 400


def test_save_to_obsidian_folders(client):
    import os
    d = rich_meeting(client)
    root = Path(os.environ["MEMORY_DESK_CONFIG"]).parent / "Claude"
    for kind, folder in (("memo", "Служебные записки"), ("brief", "Брифы"), ("followup", "Письма")):
        out = client.post("/api/materials/artifact", json={"kind": kind, "meetingId": d["id"], "save": True}).json()
        target = root / folder / out["fileName"]
        assert target.is_file() and target.read_text(encoding="utf-8") == out["markdown"]
        assert out["savedAs"].endswith(out["fileName"]) and out["obsidianUrl"].startswith("obsidian://")
    r = client.post("/api/materials/save", json={"kind": "memo", "name": "Правка записки", "content": "# Записка\n- пункт `⏱ 00:00:05`\n"})
    assert r.status_code == 200 and (root / "Служебные записки" / "Правка записки.md").is_file()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("fmt", ("docx", "pdf", "html", "md"))
def test_export_through_stage_41(client, kind, fmt):
    project = project_with_records(client)
    out = make(client, kind, projectId=project["id"], days=3650)
    r = client.post("/api/export", json={"format": fmt, "title": out["title"], "markdown": out["markdown"], "name": "Документ"})
    assert r.status_code == 200 and len(r.content) > 200
    if fmt == "docx":
        assert r.content[:2] == b"PK"
    if fmt == "pdf":
        assert r.content[:4] == b"%PDF"


def test_chat_requests(client):
    d = rich_meeting(client)
    project = project_with_records(client)
    ask = lambda q, scope=None: client.post("/api/chat", json={"question": q, "scope": scope or {"kind": "all"}}).json()
    record = {"kind": "record", "id": d["id"]}
    memo = ask("составь служебную записку", record)
    assert memo["material"]["kind"] == "memo" and memo["material"]["result"]["markdown"].startswith("# Служебная записка")
    assert "⏱" in memo["answer"]
    assert ask("подготовь бриф", record)["material"]["kind"] == "brief"
    assert ask("сделай follow-up письмо участникам", record)["material"]["kind"] == "followup"
    assert ask(f"подготовь бриф по проекту {project['title']}")["material"]["kind"] == "brief"
    # обычные вопросы не считаются просьбой
    assert "material" not in ask("о чём письмо от заказчика?")
    assert "material" not in ask("какой бриф был у проекта?")


def test_request_kind_patterns():
    k = materials.request_kind
    assert k("составь служебную записку руководителю") == "memo"
    assert k("сделай бриф по проекту") == "brief"
    assert k("подготовь follow-up письмо") == "followup"
    assert k("составь итоговое письмо участникам") == "followup"
    assert k("напиши письмо заказчику") is None
    assert k("составь протокол") == "protocol" and k("сделай презентацию") == "deck"
