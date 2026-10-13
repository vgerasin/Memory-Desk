"""Этап 6: журнал действий (6.1), «стереть запись целиком» (6.2), блокировка PIN (6.3)."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TITLE = "Разговор о справочнике"


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import meetings_process
    import memory_desk.audit, memory_desk.config, memory_desk.erase, memory_desk.knowledge, memory_desk.storage  # noqa: F401
    import test_stage3 as s3
    s3.mp, s3.knowledge = meetings_process, memory_desk.knowledge
    globals().update(audit=memory_desk.audit, config=memory_desk.config, erase=memory_desk.erase,
                     knowledge=memory_desk.knowledge, storage=memory_desk.storage, s3=s3)


@pytest.fixture()
def note(client, tmp_path, monkeypatch):
    # DATA_DIR не подменяется: в нём база, которую читают фоновые потоки; журнал уходит во временную папку
    monkeypatch.setattr(audit, "path", lambda: tmp_path / "audit.log")
    created = s3.Note(client).create()
    yield created
    created.remove()


def lines() -> list[dict]:
    file = audit.path()
    return [json.loads(x) for x in file.read_text(encoding="utf-8").splitlines()] if file.exists() else []


def last(action: str) -> dict:
    return next(r for r in reversed(lines()) if r["action"] == action)


def task_of(client, note):
    return client.get(f"/api/meetings/{note.id}").json()["tasks"][0]


# --------------------------------------------------------------------------- 6.1 журнал

def test_every_mutating_route_is_classified(client):
    """Новый маршрут, меняющий данные, не может остаться вне журнала: его нужно внести в каталог или в список «только чтение»."""
    unknown = []
    for route in client.app.routes:
        for method in (getattr(route, "methods", None) or set()) - {"GET", "HEAD", "OPTIONS"}:
            key = (method, route.path)
            if route.path.startswith("/api") and key not in audit.ACTIONS and key not in audit.READ_ONLY:
                unknown.append(key)
    assert unknown == []


def test_action_is_logged_with_ids_and_without_content(client, note):
    task = task_of(client, note)
    secret = "Совершенно секретная формулировка задачи"
    assert client.patch(f"/api/tasks/{task['id']}", json={"text": secret, "owner": "Иван Петров"}).status_code == 200
    row = last("task.update")
    assert row["actor"] == "cabinet" and row["result"] == "ok" and row["id"] == task["id"] and row["target"] == "task"
    assert row["detail"] == "поля: text, owner"
    text = audit.path().read_text(encoding="utf-8")
    assert secret not in text and "Иван Петров" not in text and TITLE not in text


def test_failures_and_denials_are_logged(client, note):
    task = task_of(client, note)
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "летает"}).status_code == 400
    assert last("task.update")["result"] == "error"
    assert client.patch("/api/tasks/нет-такой", json={"status": "open"}).status_code in (400, 404)
    assert last("task.update")["id"] == "нет-такой"
    # запрос без служебного заголовка отклоняется до маршрута и в журнал как действие не попадает
    before = len(lines())
    assert client.post("/api/system/backup", headers={"x-memory-desk": ""}).status_code == 403
    assert len(lines()) == before


def test_read_only_requests_are_not_logged(client, note, monkeypatch):
    before = len(lines())
    client.post("/api/chat", json={"question": "Что решили?", "scope": {"kind": "all"}})
    client.post("/api/export", json={"markdown": "# x", "format": "md", "title": "x"})
    client.get("/api/meetings")
    assert len(lines()) == before


def test_bulk_and_settings_details_have_counts_not_content(client, note):
    task = task_of(client, note)
    client.post("/api/tasks/bulk", json={"ids": [task["id"]], "status": "open"})
    assert last("task.bulk")["detail"] == "элементов: 1; статус: open"
    settings = client.get("/api/settings").json()
    keep = settings["retention_days"]
    client.put("/api/settings", json={**{k: settings[k] for k in settings if k != "exists"}, "retention_days": keep + 1})
    assert "retention_days" in last("settings.update")["detail"]
    client.put("/api/settings", json={**{k: settings[k] for k in settings if k != "exists"}, "retention_days": keep})


def test_chain_detects_edit_and_deletion(client, note):
    for sec in (1, 2, 3):
        client.post(f"/api/meetings/{note.id}/bookmarks", json={"sec": sec})
    assert audit.verify() == {"ok": True, "count": 3, "badLine": None, "message": "цепочка цела"}
    file = audit.path()
    original = file.read_text(encoding="utf-8")
    rows = original.splitlines()
    try:
        file.write_text("\n".join([rows[0].replace('"ok"', '"error"', 1)] + rows[1:]) + "\n", encoding="utf-8")
        assert audit.verify()["ok"] is False and audit.verify()["badLine"] == 1           # строку исправили
        file.write_text("\n".join(rows[1:]) + "\n", encoding="utf-8")
        assert audit.verify()["ok"] is False and audit.verify()["badLine"] == 1           # первую вырезали
        file.write_text("\n".join(rows[:1] + rows[2:]) + "\n", encoding="utf-8")
        assert audit.verify()["ok"] is False and audit.verify()["badLine"] == 2           # вырезали из середины
        file.write_text(original + "не JSON\n", encoding="utf-8")
        assert audit.verify()["ok"] is False
    finally:
        file.write_text(original, encoding="utf-8")
        audit._tail.clear()
    assert audit.verify()["ok"] is True
    client.post(f"/api/meetings/{note.id}/bookmarks", json={"sec": 4})            # после перезапуска цепочка продолжается
    assert audit.verify()["ok"] is True and audit.verify()["count"] == 4


def test_listing_filters_names_and_csv(client, note):
    task = task_of(client, note)
    client.patch(f"/api/tasks/{task['id']}", json={"status": "open"})
    client.post(f"/api/meetings/{note.id}/bookmarks", json={"sec": 10, "text": "тест"})
    data = client.get("/api/audit", params={"kind": "task.", "limit": 5}).json()
    assert data["total"] >= 1 and data["items"][0]["action"].startswith("task.")
    assert data["items"][0]["label"] == "Правка задачи" and data["items"][0]["object"] == f"задача из «{TITLE}»"
    assert data["chain"]["ok"] is True and "cabinet" in data["actors"]
    marks = client.get("/api/audit", params={"q": note.id}).json()
    assert marks["total"] >= 1 and all(note.id in json.dumps(i, ensure_ascii=False) for i in marks["items"])
    assert any(i["object"] == f"«{TITLE}»" for i in marks["items"])
    assert client.get("/api/audit", params={"result": "error", "q": "нет-такого-слова"}).json()["total"] == 0
    assert client.get("/api/audit", params={"since": "2999-01-01"}).json()["total"] == 0
    assert client.get("/api/audit", params={"actor": "system", "kind": "нет."}).json()["items"] == []
    csv = client.get("/api/audit/export", params={"kind": "bookmark."})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"] and csv.content.startswith("﻿".encode())
    body = csv.content.decode("utf-8-sig")
    assert body.splitlines()[0].startswith("Время;Кто;Действие") and "Закладка на записи" in body and f"«{TITLE}»" in body


def test_logging_failure_does_not_break_the_action(client, note, monkeypatch, tmp_path):
    blocked = tmp_path / "файл-вместо-папки"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setattr(audit, "path", lambda: blocked / "audit.log")
    r = client.post(f"/api/meetings/{note.id}/bookmarks", json={"sec": 11})
    assert r.status_code == 200


def test_system_events_are_logged(client, note):
    watcher = client.app.state.watcher
    watcher._last_backup_check = 0
    watcher.core.data_version += 1
    before = len(lines())
    assert watcher.backup_if_due(now=time.time() + 10 ** 7) is True
    row = last("backup.auto")
    assert row["actor"] == "system" and len(lines()) > before


# --------------------------------------------------------------------------- 6.2 стереть запись

@pytest.fixture()
def full_record(client, note, tmp_path, monkeypatch):
    """Запись со всем, что должно исчезнуть при стирании."""
    core = client.app.state.core
    meeting = core.by_id[note.id]
    archive = knowledge.ARCHIVE_ROOT
    archive.mkdir(parents=True, exist_ok=True)
    audio = archive / meeting.source_audio
    audio.write_bytes(b"ID3audio")
    from memory_desk import attachments, ocr
    monkeypatch.setattr(ocr, "BACKEND", lambda p: ocr.Result("Текст с доски", "Vision"))
    from PIL import Image
    import io
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (1, 2, 3)).save(buffer, "PNG")
    att = client.post(f"/api/meetings/{note.id}/attachments", params={"name": "доска.png"}, content=buffer.getvalue(),
                      headers={"content-type": "application/octet-stream"}).json()
    client.post(f"/api/meetings/{note.id}/bookmarks", json={"sec": 20, "text": "закладка"})
    with storage.connect() as db:
        for table in ("meeting_overlaps", "meeting_voices", "processing_events"):
            db.execute(f"DELETE FROM {table} WHERE meeting_id=?", (note.id,))
        db.execute("INSERT INTO meeting_overlaps (meeting_id, sec) VALUES (?, 5)", (note.id,))
        db.execute("INSERT INTO meeting_voices (meeting_id,label,embedding,seconds,matched,score) VALUES (?,?,?,?,0,NULL)",
                   (note.id, "Спикер 1", b"\x00" * 8, 3.0))
        db.execute("INSERT INTO processing_events (meeting_id,event,detail,created_at) VALUES (?,?,?,?)", (note.id, "done", "x", storage.now_iso()))
    wave = config.DATA_DIR / "waveforms" / f"{note.id}.json"
    wave.parent.mkdir(parents=True, exist_ok=True)
    wave.write_text("{}", encoding="utf-8")
    copy_dir = config.DATA_DIR / "note-backups"
    copy_dir.mkdir(parents=True, exist_ok=True)
    copy = copy_dir / f"{meeting.path.stem}.20261013-101010.md"
    copy.write_text("старая копия заметки", encoding="utf-8")
    protocol_dir = knowledge.WORKSPACE_DIR / "Протоколы"
    protocol_dir.mkdir(parents=True, exist_ok=True)
    protocol = protocol_dir / "test-stage6 — протокол.md"
    protocol.write_text(f"# Протокол\nсм. [[{meeting.path.stem}]]\n", encoding="utf-8")
    other = protocol_dir / "test-stage6 — чужой.md"
    other.write_text("# Совсем другой документ", encoding="utf-8")
    storage.create_backup()
    work = config.PROJECT_ROOT / "work" / "test-stage6-work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "state.json").write_text(json.dumps({"note": str(meeting.path), "status": "completed"}), encoding="utf-8")
    (work / "transcript.json").write_text("{}", encoding="utf-8")
    yield {"audio": audio, "att": att, "wave": wave, "copy": copy, "protocol": protocol, "other": other, "work": work,
           "meeting": meeting}
    for path in (protocol, other, audio, wave, copy):
        path.unlink(missing_ok=True)
    with storage.connect() as db:
        for table in ("meeting_overlaps", "meeting_voices", "processing_events"):
            db.execute(f"DELETE FROM {table} WHERE meeting_id=?", (note.id,))
    import shutil
    shutil.rmtree(work, ignore_errors=True)


def rows_of(meeting_id: str) -> dict:
    return {t: storage.one(f"SELECT COUNT(*) n FROM {t} WHERE meeting_id=?", (meeting_id,))["n"]
            for t in ("tasks", "decisions", "meeting_people", "meeting_voices", "meeting_overlaps", "processing_events")} \
        | {"meetings": storage.one("SELECT COUNT(*) n FROM meetings WHERE id=?", (meeting_id,))["n"]}


def test_plan_lists_everything_and_changes_nothing(client, note, full_record):
    meeting = full_record["meeting"]
    before = {p: p.read_bytes() for p in (meeting.path, full_record["audio"])}
    plan = client.get(f"/api/meetings/{note.id}/erase/plan").json()
    items = {i["key"]: i for i in plan["items"]}
    assert plan["confirmWord"] == "СТЕРЕТЬ" and plan["title"] == TITLE
    assert items["audio"]["count"] == 1 and items["attachments"]["count"] == 1 and items["transcript"]["count"] == 1
    assert items["work"]["count"] == 1 and items["waveform"]["count"] == 1 and items["noteCopies"]["count"] == 1
    assert items["backups"]["count"] >= 1 and items["registry"]["count"] > 3
    assert [d["name"] for d in plan["documents"]] == ["test-stage6 — протокол.md"]
    assert any("Time Machine" in w for w in plan["warnings"])
    assert {p: p.read_bytes() for p in before} == before
    assert client.get("/api/meetings/нет/erase/plan").status_code == 404


def test_confirmation_word_is_required(client, note, full_record):
    for bad in ("", "стереть?", "да", "удалить"):
        r = client.post(f"/api/meetings/{note.id}/erase", json={"confirm": bad})
        assert r.status_code == 400 and "СТЕРЕТЬ" in r.json()["detail"]
    assert full_record["meeting"].path.exists() and full_record["audio"].exists()
    assert client.post("/api/meetings/нет/erase", json={"confirm": "СТЕРЕТЬ"}).status_code == 404


def test_erase_removes_everything_and_nothing_else(client, note, full_record):
    core = client.app.state.core
    others = {m["id"] for m in client.get("/api/meetings", params={"limit": 1000}).json()["items"]} - {note.id}
    other_rows = {i: rows_of(i) for i in others}
    anna = storage.one("SELECT id FROM people LIMIT 1")["id"]
    with storage.connect() as db:
        db.execute("INSERT OR REPLACE INTO voice_profiles (person_id,embedding,samples,seconds,updated_at) VALUES (?,?,?,?,?)",
                   (anna, b"\x01" * 8, 2, 9.0, storage.now_iso()))
    meeting = full_record["meeting"]
    transcript = knowledge.transcript_file(meeting)
    from qdrant_client import models
    only_this = models.Filter(must=[models.FieldCondition(key="meeting_id", match=models.MatchValue(value=note.id))])
    core.index.sync(list(core.meetings), force=True)
    assert core.index.client.scroll("meetings", scroll_filter=only_this, limit=5)[0], "до стирания фрагменты записи должны быть в индексе"
    r = client.post(f"/api/meetings/{note.id}/erase", json={"confirm": " стереть ", "documents": [client.get(f"/api/meetings/{note.id}/erase/plan").json()["documents"][0]["id"]]})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["ok"] is True and result["audio"] == 1 and result["attachments"] == 1 and result["note"] == 1
    assert result["documents"] == 1 and result["noteCopies"] == 1 and result["problems"] == []
    # файлы
    assert not meeting.path.exists() and not transcript.exists() and not full_record["audio"].exists()
    assert not (meeting.path.parent / "Вложения").exists() and not full_record["wave"].exists() and not full_record["copy"].exists()
    assert not full_record["work"].exists() and not full_record["protocol"].exists()
    assert full_record["other"].exists()                                         # неотмеченный документ остался
    # запись не находится ни по ссылке, ни в списках, ни в базе
    assert client.get(f"/api/meetings/{note.id}").status_code == 404
    assert note.id not in {m["id"] for m in client.get("/api/meetings", params={"limit": 1000}).json()["items"]}
    assert all(v == 0 for v in rows_of(note.id).values())
    assert not any(t["meetingId"] == note.id for t in client.get("/api/tasks", params={"kind": "tasks", "limit": 1000}).json()["items"])
    # поисковый индекс
    assert core.index.client.scroll("meetings", scroll_filter=only_this, limit=5)[0] == []
    # копия базы очищена, остальное в ней цело
    for backup in storage.list_backups():
        db = sqlite3.connect(backup)
        try:
            assert db.execute("SELECT COUNT(*) FROM meetings WHERE id=?", (note.id,)).fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] >= len(others)
        finally:
            db.close()
    # другие записи и голос человека не пострадали
    assert {i: rows_of(i) for i in others} == other_rows
    assert storage.one("SELECT samples FROM voice_profiles WHERE person_id=?", (anna,))["samples"] == 2
    with storage.connect() as db:
        db.execute("DELETE FROM voice_profiles WHERE person_id=?", (anna,))
    # журнал: факт стирания есть, названия и содержимого нет
    row = last("record.erase")
    assert row["id"] == note.id and row["result"] == "ok" and "audio: 1" in row["detail"]
    assert TITLE not in audit.path().read_text(encoding="utf-8")
    listed = client.get("/api/audit", params={"kind": "record.erase"}).json()["items"][0]
    assert listed["object"] == "стёртая запись"
    # повтор: записи уже нет
    assert client.post(f"/api/meetings/{note.id}/erase", json={"confirm": "СТЕРЕТЬ"}).status_code == 404


def test_backups_can_be_left_alone(client, note, full_record):
    assert client.post(f"/api/meetings/{note.id}/erase", json={"confirm": "СТЕРЕТЬ", "scrubBackups": False, "noteCopies": False}).status_code == 200
    assert full_record["copy"].exists()
    assert any(sqlite3.connect(b).execute("SELECT COUNT(*) FROM meetings WHERE id=?", (note.id,)).fetchone()[0] for b in storage.list_backups())


def test_erase_refused_while_processing(client, note, full_record, monkeypatch):
    monkeypatch.setattr(erase, "_busy", lambda: True)
    r = client.post(f"/api/meetings/{note.id}/erase", json={"confirm": "СТЕРЕТЬ"})
    assert r.status_code == 409 and full_record["meeting"].path.exists()


def test_interrupted_erase_keeps_note_and_can_be_repeated(client, note, full_record, monkeypatch):
    """Не удалился файл (занят, нет прав): заметка и строки базы остаются, повтор доводит стирание до конца."""
    real_unlink = erase._unlink

    def flaky(path):
        if path == full_record["audio"]:
            raise OSError("занято")
        return real_unlink(path)
    monkeypatch.setattr(erase, "_unlink", flaky)
    first = client.post(f"/api/meetings/{note.id}/erase", json={"confirm": "СТЕРЕТЬ"}).json()
    assert first["ok"] is False and first["interrupted"] is True and any("audio" in p for p in first["problems"])
    assert full_record["meeting"].path.exists() and full_record["audio"].exists() and rows_of(note.id)["meetings"] == 1
    assert client.get(f"/api/meetings/{note.id}").status_code == 200
    assert last("record.erase")["detail"].endswith("сбоев: 1")
    monkeypatch.setattr(erase, "_unlink", real_unlink)
    again = client.post(f"/api/meetings/{note.id}/erase", json={"confirm": "СТЕРЕТЬ"}).json()
    assert again["ok"] is True and not full_record["audio"].exists() and not full_record["meeting"].path.exists()
    assert all(v == 0 for v in rows_of(note.id).values())


# --------------------------------------------------------------------------- 6.3 блокировка PIN

@pytest.fixture()
def pin_env(client, tmp_path, monkeypatch):
    """PIN, журнал и счётчик попыток — во временной папке; общий `client` после теста снова без PIN."""
    import memory_desk.lock as lock
    monkeypatch.setattr(audit, "path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(lock, "_file", lambda: tmp_path / "lock.json")
    monkeypatch.setattr(lock, "_state_file", lambda: tmp_path / "lock-state.json")
    monkeypatch.setattr(lock, "_have_keychain", lambda: False)
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(lock, "now", lambda: clock["t"])
    handle = client.app.state.lock
    handle.close()
    handle.forget_cache()
    old_timeout = client.app.state.core.cfg.get("lock_timeout_min", 15)

    class Env:
        pass
    env = Env()
    env.lock, env.clock, env.client, env.dir = lock, clock, client, tmp_path
    env.advance = lambda seconds: clock.__setitem__("t", clock["t"] + seconds)
    yield env
    lock.reset()
    handle.close()
    handle.forget_cache()
    client.cookies.clear()
    client.app.state.core.cfg = config.save_config({**client.app.state.core.cfg, "lock_timeout_min": old_timeout})


def set_pin(env, pin="2468", current=""):
    return env.client.post("/api/lock/pin", json={"pin": pin, "current": current})


def other_browser(env):
    """Запрос «из другого браузера»: без cookie сессии."""
    env.client.cookies.clear()


def test_without_pin_nothing_is_locked(pin_env):
    status = pin_env.client.get("/api/lock/status").json()
    assert status["enabled"] is False and status["locked"] is False
    assert pin_env.client.get("/api/meetings").status_code == 200


def test_pin_must_be_digits_of_sensible_length(pin_env):
    for bad in ("12", "abcd", "1234567890123", "12 4"):
        assert set_pin(pin_env, bad).status_code == 400
    assert pin_env.client.get("/api/lock/status").json()["enabled"] is False


def test_api_refuses_without_pin_and_shows_no_content(pin_env):
    assert set_pin(pin_env).status_code == 200
    other_browser(pin_env)
    status = pin_env.client.get("/api/lock/status").json()
    assert status["enabled"] and status["locked"]
    assert pin_env.client.get("/api/health").status_code == 200
    for method, url in (("get", "/api/meetings"), ("get", "/api/tasks"), ("get", "/api/state"), ("get", "/api/settings"),
                        ("get", "/api/audit"), ("get", "/api/search?q=привет"), ("post", "/api/system/backup"),
                        ("post", "/api/meetings/bulk"), ("post", "/api/lock/ping")):
        response = getattr(pin_env.client, method)(url, **({"json": {}} if method == "post" else {}))
        assert response.status_code == 423, url
        assert response.json() == {"detail": "Заблокировано: введите PIN", "locked": True}
    blocked = [r for r in lines() if r["action"] == "lock.blocked"]
    assert blocked and all(r["result"] == "denied" for r in blocked)


def test_wrong_pin_then_right_pin(pin_env):
    set_pin(pin_env)
    other_browser(pin_env)
    wrong = pin_env.client.post("/api/lock/unlock", json={"pin": "0000"})
    assert wrong.status_code == 401 and "Неверный PIN" in wrong.json()["detail"]
    assert pin_env.client.get("/api/meetings").status_code == 423
    right = pin_env.client.post("/api/lock/unlock", json={"pin": "2468"})
    assert right.status_code == 200 and right.json()["locked"] is False
    cookie = right.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert pin_env.client.get("/api/meetings").status_code == 200


def test_pin_is_stored_only_as_hash(pin_env):
    set_pin(pin_env, "135790")
    file = pin_env.dir / "lock.json"
    text = file.read_text(encoding="utf-8")
    assert "135790" not in text and '"scrypt"' in text
    assert (file.stat().st_mode & 0o777) == 0o600
    assert "135790" not in "".join(p.read_text(errors="ignore") for p in pin_env.dir.glob("*.log"))


def test_attempts_slow_down_and_survive_restart(pin_env):
    lock = pin_env.lock
    set_pin(pin_env)
    other_browser(pin_env)
    answers = [pin_env.client.post("/api/lock/unlock", json={"pin": "1111"}) for _ in range(2)]
    assert all(a.status_code == 401 and a.json()["retryAfter"] == 0 for a in answers)
    third = pin_env.client.post("/api/lock/unlock", json={"pin": "1111"})
    assert third.status_code == 401 and third.json()["retryAfter"] == 5
    early = pin_env.client.post("/api/lock/unlock", json={"pin": "2468"})
    assert early.status_code == 429 and early.json()["retryAfter"] > 0 and early.headers["retry-after"]
    assert lock.Lock(None).retry_after() > 0       # новый экземпляр (перезапуск) видит ту же задержку
    pin_env.advance(6)
    assert pin_env.client.post("/api/lock/unlock", json={"pin": "1111"}).json()["retryAfter"] == 10
    pin_env.advance(11)
    assert pin_env.client.post("/api/lock/unlock", json={"pin": "1111"}).json()["retryAfter"] == 20
    pin_env.advance(21)
    assert pin_env.client.post("/api/lock/unlock", json={"pin": "2468"}).status_code == 200
    assert lock.Lock(None).retry_after() == 0
    assert [lock.delay_after(n) for n in (1, 3, 4, 5, 20)] == [0, 5, 10, 20, 900]


def test_idle_timeout_counts_actions_but_not_background_polling(pin_env):
    set_pin(pin_env)
    other_browser(pin_env)
    pin_env.client.post("/api/lock/unlock", json={"pin": "2468"})
    pin_env.client.put("/api/lock/timeout", json={"minutes": 1})
    pin_env.advance(50)
    assert pin_env.client.get("/api/meetings").status_code == 200          # фоновое чтение сессию не продлевает
    pin_env.advance(20)
    assert pin_env.client.get("/api/meetings").status_code == 423
    pin_env.client.post("/api/lock/unlock", json={"pin": "2468"})
    pin_env.advance(50)
    assert pin_env.client.post("/api/lock/ping").status_code == 200        # «я здесь» продлевает
    pin_env.advance(50)
    assert pin_env.client.get("/api/meetings").status_code == 200
    pin_env.advance(61)
    assert pin_env.client.get("/api/meetings").status_code == 423
    assert any(r["action"] == "lock.timeout" and r["actor"] == "system" for r in lines())


def test_session_ends_after_twelve_hours_even_if_active(pin_env):
    set_pin(pin_env)
    pin_env.client.put("/api/lock/timeout", json={"minutes": 60})
    for _ in range(15):
        pin_env.advance(50 * 60)
        pin_env.client.post("/api/lock/ping")
    assert pin_env.client.get("/api/meetings").status_code == 423


def test_lock_button_closes_session(pin_env):
    set_pin(pin_env)
    assert pin_env.client.get("/api/meetings").status_code == 200
    assert pin_env.client.post("/api/lock/lock").json()["locked"] is True
    assert pin_env.client.get("/api/meetings").status_code == 423


def test_changing_and_removing_pin_need_the_current_one(pin_env):
    set_pin(pin_env)
    assert set_pin(pin_env, "9999", "0000").status_code == 401
    assert pin_env.client.post("/api/lock/remove", json={"current": "0000"}).status_code == 401
    assert set_pin(pin_env, "9999", "2468").status_code == 200
    other_browser(pin_env)
    assert pin_env.client.post("/api/lock/unlock", json={"pin": "2468"}).status_code == 401
    pin_env.advance(100)
    assert pin_env.client.post("/api/lock/unlock", json={"pin": "9999"}).status_code == 200
    removed = pin_env.client.post("/api/lock/remove", json={"current": "9999"})
    assert removed.status_code == 200 and removed.json()["enabled"] is False
    assert not (pin_env.dir / "lock.json").exists()
    other_browser(pin_env)
    assert pin_env.client.get("/api/meetings").status_code == 200


def test_changing_pin_closes_other_sessions(pin_env):
    set_pin(pin_env)
    old_cookie = dict(pin_env.client.cookies)
    set_pin(pin_env, "7777", "2468")
    pin_env.client.cookies.clear()
    for name, value in old_cookie.items():
        pin_env.client.cookies.set(name, value)
    assert pin_env.client.get("/api/meetings").status_code == 423


def test_timeout_setting_is_validated_and_kept_by_settings_save(pin_env):
    assert pin_env.client.put("/api/lock/timeout", json={"minutes": 7}).status_code == 400
    assert pin_env.client.put("/api/lock/timeout", json={"minutes": 30}).json()["timeoutMin"] == 30
    body = {k: v for k, v in pin_env.client.get("/api/settings").json().items() if k not in ("exists", "lock_timeout_min")}
    assert pin_env.client.put("/api/settings", json=body).status_code == 200
    assert pin_env.client.get("/api/lock/status").json()["timeoutMin"] == 30


def test_lock_actions_are_logged_without_the_pin(pin_env):
    set_pin(pin_env, "314159")
    other_browser(pin_env)
    pin_env.client.post("/api/lock/unlock", json={"pin": "271828"})
    pin_env.advance(10)
    pin_env.client.post("/api/lock/unlock", json={"pin": "314159"})
    pin_env.client.post("/api/lock/lock")
    codes = [(r["action"], r["result"]) for r in lines()]
    assert ("lock.pin", "ok") in codes and ("lock.unlock", "denied") in codes
    assert ("lock.unlock", "ok") in codes and ("lock.lock", "ok") in codes
    raw = audit.path().read_text(encoding="utf-8")
    assert "314159" not in raw and "271828" not in raw


def test_keychain_is_used_when_available(pin_env, monkeypatch):
    lock = pin_env.lock
    store: dict[str, str] = {}

    class Done:
        def __init__(self, code=0, out=""):
            self.returncode, self.stdout = code, out

    def fake_run(cmd, **kwargs):
        verb = cmd[1]
        if verb == "add-generic-password":
            store["value"] = cmd[cmd.index("-w") + 1]
            return Done()
        if verb == "find-generic-password":
            return Done(0, store["value"] + "\n") if "value" in store else Done(44)
        if verb == "delete-generic-password":
            store.clear()
            return Done()
        return Done(1)

    monkeypatch.setattr(lock, "_have_keychain", lambda: True)
    monkeypatch.setattr(lock.subprocess, "run", fake_run)
    answer = set_pin(pin_env, "4321")
    assert answer.status_code == 200 and answer.json()["storage"] == "keychain"
    assert not (pin_env.dir / "lock.json").exists() and "4321" not in store["value"]
    other_browser(pin_env)
    assert pin_env.client.post("/api/lock/unlock", json={"pin": "4321"}).status_code == 200
    assert pin_env.client.post("/api/lock/remove", json={"current": "4321"}).status_code == 200
    assert store == {}


def test_reset_script_removes_pin(pin_env):
    set_pin(pin_env)
    pin_env.lock.reset()
    pin_env.client.app.state.lock.forget_cache()
    other_browser(pin_env)
    assert pin_env.client.get("/api/meetings").status_code == 200
