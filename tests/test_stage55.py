"""Этап 5.5: состояние задач и решений в базе, сводки в Obsidian, копии после изменений, проверка базы."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import memory_desk.app, memory_desk.config, memory_desk.knowledge, memory_desk.mirror, memory_desk.storage  # noqa: F401
    globals().update(app_module=memory_desk.app, config=memory_desk.config, knowledge=memory_desk.knowledge,
                     mirror=memory_desk.mirror, storage=memory_desk.storage)


def note_of(client, meeting_id):
    core = client.app.state.core
    return core.by_id[meeting_id].path


def digest_of(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def meeting_with_tasks(client):
    for m in client.get("/api/meetings", params={"limit": 1000}).json()["items"]:
        d = client.get(f"/api/meetings/{m['id']}").json()
        if d["tasks"] and d["decisions"]:
            return d
    raise AssertionError("нет записи с задачами и решениями")


# --------------------------------------------------------------------------- заметка записи не меняется

def test_edits_do_not_touch_the_note_by_default(client):
    assert client.app.state.core.cfg["note_writeback"] is False
    d = meeting_with_tasks(client)
    path = note_of(client, d["id"])
    before, mtime = digest_of(path), path.stat().st_mtime_ns
    task, decision = d["tasks"][0], d["decisions"][0]
    r = client.patch(f"/api/tasks/{task['id']}", json={"status": "done", "owner": "Пётр Новый", "deadline": "2027-01-15"})
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["owner"] == "Пётр Новый"
    assert r.json()["noteSynced"] is True        # синхронизировать нечего: заметка не затрагивается
    r = client.patch(f"/api/decisions/{decision['id']}", json={"status": "confirmed"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert digest_of(path) == before and path.stat().st_mtime_ns == mtime
    # состояние читается из базы
    again = client.get(f"/api/meetings/{d['id']}").json()
    assert next(t for t in again["tasks"] if t["id"] == task["id"])["owner"] == "Пётр Новый"
    # ничего не ломается при пересборке индекса: правки не затираются
    client.post("/api/system/reindex")
    again = client.get(f"/api/meetings/{d['id']}").json()
    assert next(t for t in again["tasks"] if t["id"] == task["id"])["status"] == "done"
    client.patch(f"/api/tasks/{task['id']}", json={"status": task["status"], "owner": task["owner"] or "не указано", "deadline": task["due"] or "не указан"})
    client.patch(f"/api/decisions/{decision['id']}", json={"status": decision["status"]})


def test_writeback_can_be_enabled(client, monkeypatch):
    monkeypatch.setitem(client.app.state.core.cfg, "note_writeback", True)
    d = next(x for x in (client.get(f"/api/meetings/{m['id']}").json() for m in client.get("/api/meetings", params={"limit": 1000}).json()["items"])
             if x["tasks"] and any(("- [ ] " + x["tasks"][0]["text"]) in l for l in note_of(client, x["id"]).read_text(encoding="utf-8").splitlines()))
    path = note_of(client, d["id"])
    task = d["tasks"][0]
    before = digest_of(path)
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).status_code == 200
    assert digest_of(path) != before and ("- [x] " + task["text"]) in path.read_text(encoding="utf-8")
    client.patch(f"/api/tasks/{task['id']}", json={"status": "open"})


def test_settings_roundtrip(client):
    s = client.get("/api/settings").json()
    assert s["note_writeback"] is False and s["mirror_enabled"] is True and s["backup_on_change"] is True
    body = {k: v for k, v in s.items() if k != "exists"}
    body.update(note_writeback=True, mirror_enabled=False, backup_on_change=False)
    assert client.put("/api/settings", json=body).status_code == 200
    s = client.get("/api/settings").json()
    assert s["note_writeback"] is True and s["mirror_enabled"] is False and s["backup_on_change"] is False
    body.update(note_writeback=False, mirror_enabled=True, backup_on_change=True)
    assert client.put("/api/settings", json=body).status_code == 200
    assert client.get("/api/settings").json()["note_writeback"] is False


# --------------------------------------------------------------------------- сводные файлы

def test_mirror_files_content(client):
    out = client.post("/api/system/mirror").json()
    assert out["folder"] == "Сводки" and set(out["files"]) == {"Задачи.md", "Решения.md"}
    tasks = (mirror.folder() / "Задачи.md").read_text(encoding="utf-8")
    decisions = (mirror.folder() / "Решения.md").read_text(encoding="utf-8")
    assert tasks.startswith("---\ntype: mirror\n") and "автоматически" in tasks and decisions.startswith("---\ntype: mirror\n")
    assert "# Задачи" in tasks and "# Решения" in decisions
    # у каждой строки ссылка на запись; у большинства есть таймкод
    items = [l for l in tasks.splitlines() if l.startswith("- [")]
    assert items and all("[[" in l and "|" in l for l in items)
    assert any("`⏱ " in l for l in items)
    # задача из базы видна, группы по проектам и статусам
    d = meeting_with_tasks(client)
    assert any(t["text"][:30] in tasks for t in d["tasks"] if t["status"] in ("draft", "open"))
    assert "### Ждут подтверждения" in tasks or "### В работе" in tasks
    assert "### Действующие" in decisions
    assert all(l.startswith("- ") for l in decisions.splitlines() if l.startswith(("- ", "- ~~")))


def test_mirror_follows_status_and_replacements(client):
    d = meeting_with_tasks(client)
    task = d["tasks"][0]
    client.post("/api/system/mirror")
    path = mirror.folder() / "Задачи.md"
    before_stat = path.stat().st_mtime_ns
    # без изменений файл не переписывается (Obsidian не видит лишних правок)
    assert client.post("/api/system/mirror").json()["written"] == []
    assert path.stat().st_mtime_ns == before_stat
    # закрыли задачу — она переходит в «Выполнено»
    client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert client.post("/api/system/mirror").json()["written"] == ["Задачи.md"]
    text = path.read_text(encoding="utf-8")
    done = text.split("### Выполнено", 1)[1] if "### Выполнено" in text else ""
    assert any(l.startswith("- [x] ") and task["text"][:30] in l for l in done.splitlines())
    # отклонили — пропала совсем
    client.patch(f"/api/tasks/{task['id']}", json={"status": "rejected"})
    client.post("/api/system/mirror")
    assert task["text"][:30] not in path.read_text(encoding="utf-8")
    client.patch(f"/api/tasks/{task['id']}", json={"status": task["status"]})


def test_mirror_replaced_decision_is_struck_out(client):
    decisions = client.get("/api/tasks", params={"kind": "decisions", "limit": 1000}).json()["items"]
    pair = {}
    for x in decisions:
        pair.setdefault(x["meetingId"], []).append(x)
    group = next((v for v in pair.values() if len(v) >= 2), None)
    if group is None:
        pytest.skip("нет записи с двумя решениями")
    old, new = group[0], group[1]
    assert client.patch(f"/api/decisions/{old['id']}", json={"replaced_by": new["id"]}).status_code == 200
    try:
        client.post("/api/system/mirror")
        text = (mirror.folder() / "Решения.md").read_text(encoding="utf-8")
        assert "### Заменены" in text and f"~~{old['text'][:30]}" in text and "→ " + new["text"][:30] in text
    finally:
        with storage.connect() as db:
            db.execute("UPDATE decisions SET replaced_by=NULL WHERE id=?", (old["id"],))


def test_watcher_writes_mirror_when_data_changes(client, monkeypatch):
    core = client.app.state.core
    # фоновая переиндексация от прежних тестов сама поднимает data_version — дождёмся её и запретим новые запуски
    monkeypatch.setattr(core, "schedule_reindex", lambda: None)
    for _ in range(80):
        if not core._reindex_event.is_set():
            break
        time.sleep(0.05)
    time.sleep(1.3)
    watcher = app_module.Watcher(core)
    watcher.mirror_if_changed()
    path = mirror.folder() / "Решения.md"
    assert path.is_file()
    assert watcher.mirror_if_changed() is False       # ничего не менялось
    d = meeting_with_tasks(client)
    task = d["tasks"][0]
    client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})   # data_version вырос
    assert watcher.mirror_if_changed() is True
    monkeypatch.setitem(core.cfg, "mirror_enabled", False)
    client.patch(f"/api/tasks/{task['id']}", json={"status": task["status"]})
    assert watcher.mirror_if_changed() is False       # выключено в настройках
    monkeypatch.setitem(core.cfg, "mirror_enabled", True)
    watcher.mirror_if_changed()


# --------------------------------------------------------------------------- копии после изменений

def test_backup_after_changes_is_throttled(client, monkeypatch):
    core = client.app.state.core
    watcher = app_module.Watcher(core)
    now = time.time() + 10_000
    storage.create_backup()
    # интервал расписания большой, но данные изменились
    monkeypatch.setitem(core.cfg, "backup_interval_hours", 24 * 30)
    monkeypatch.setitem(core.cfg, "backup_keep", 50)
    count = len(storage.list_backups())
    assert watcher.backup_if_due(now) is False and len(storage.list_backups()) == count       # данные не менялись
    core.data_version += 1
    assert watcher.backup_if_due(now + 1000) is True and len(storage.list_backups()) == count + 1
    newest = storage.list_backups()[0]
    os.utime(newest, (now + 1000, now + 1000))          # время копии — в «модельных» часах теста
    core.data_version += 1
    assert watcher.backup_if_due(now + 1700) is False                                           # прошло меньше 15 минут с прошлой копии
    assert watcher.backup_if_due(now + 1000 + 600) is False                                     # проверка не чаще раза в 10 минут
    monkeypatch.setitem(core.cfg, "backup_on_change", False)
    assert watcher.backup_if_due(now + 6000) is False                                           # выключено
    core.data_version += 1
    assert watcher.backup_if_due(now + 9000) is False


# --------------------------------------------------------------------------- целостность базы

def test_integrity_ok_and_system_row(client):
    ok, message = storage.check_integrity()
    assert ok is True
    row = next(r for r in client.get("/api/system").json()["rows"] if r["key"] == "database")
    assert row["state"] == "ok" and row["value"] == "цела"


def test_integrity_detects_corruption(tmp_path):
    good = tmp_path / "good.db"
    db = sqlite3.connect(good)
    db.execute("CREATE TABLE t (a TEXT)")
    db.executemany("INSERT INTO t VALUES (?)", [("x" * 200 + str(i),) for i in range(500)])
    db.commit(); db.close()
    assert storage.check_integrity(good) == (True, "цела")
    bad = tmp_path / "bad.db"
    data = bytearray(good.read_bytes())
    for i in range(4096, len(data), 4096 // 2):
        data[i:i + 64] = b"\xff" * 64        # портим внутренности страниц, заголовок оставляем
    bad.write_bytes(bytes(data))
    ok, message = storage.check_integrity(bad)
    assert ok is False and message
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database" * 100)
    assert storage.check_integrity(junk)[0] is False
    assert storage.check_integrity(tmp_path / "missing.db") == (True, "базы ещё нет")


def test_restore_returns_statuses_after_loss(client):
    """Потеря состояния: копия возвращает статусы задач."""
    d = meeting_with_tasks(client)
    task = d["tasks"][0]
    client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    name = client.post("/api/system/backup").json()["path"]
    client.patch(f"/api/tasks/{task['id']}", json={"status": "rejected"})
    assert task["id"] not in {t["id"] for t in client.get("/api/tasks", params={"kind": "tasks", "limit": 1000}).json()["items"] if t["status"] == "done"}
    r = client.post("/api/system/restore", json={"name": name})
    assert r.status_code == 200
    restored = client.get("/api/tasks", params={"kind": "tasks", "limit": 1000}).json()["items"]
    assert next(t for t in restored if t["id"] == task["id"])["status"] == "done"
    client.patch(f"/api/tasks/{task['id']}", json={"status": task["status"]})


def test_obsidian_url_uses_absolute_path_not_vault_name(tmp_path):
    import urllib.parse
    note = tmp_path / "Obsidian Vault" / "Claude" / "Встречи" / "2026-10-12 15-43 — Обсуждение.md"
    url = knowledge.obsidian_url(note)
    assert url.startswith("obsidian://open?path=") and "vault=" not in url
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["path"] == [note.as_posix()]
    assert "+" not in url.split("?", 1)[1]
