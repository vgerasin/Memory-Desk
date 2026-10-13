"""Этап 1: словарь терминов, уведомление о готовой записи, расписание резервных копий."""
from __future__ import annotations

import os
import time

import pytest

# Модули приложения читают окружение при импорте, поэтому импортируем их только после фикстуры `fixture_dir`.


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    import memory_desk.apple, memory_desk.dictionary, memory_desk.storage  # noqa: F401
    g = globals()
    g["apple"], g["dictionary"], g["storage"] = memory_desk.apple, memory_desk.dictionary, memory_desk.storage
    g["Entry"] = memory_desk.dictionary.Entry


# --------------------------------------------------------------------------- словарь: разбор и замены

def test_parse_dictionary_lines():
    entries, warnings = dictionary.parse(
        "# комментарий\nКХД = кахэде, ка ха дэ\n\nKafka\nкхд = кхд-хранилище\n = пусто\nФилософт=философ;фило софт\n")
    assert [e.term for e in entries] == ["КХД", "Kafka", "Философт"]
    assert entries[0].variants == ("кахэде", "ка ха дэ", "кхд-хранилище")  # повторы слова склеиваются
    assert entries[2].variants == ("философ", "фило софт")
    assert warnings == ["Строка 6: нет самого слова"]


def test_apply_text_variants_case_and_boundaries():
    entries = [Entry("КХД", ("кахэде", "ка ха дэ")), Entry("Философт", ("философ",)), Entry("kafka", ("кафка",))]
    text, n = dictionary.apply_text("Загрузка в кахэде и в Ка Ха Дэ, потом кхд. Философ сказал: философы правы. Кафка и Кафкаэск.", entries)
    assert text == "Загрузка в КХД и в КХД, потом КХД. Философт сказал: философы правы. Kafka и Кафкаэск."
    assert n == 5  # «философы» и «Кафкаэск» не тронуты: границы слов


def test_lowercase_term_keeps_sentence_capital():
    text, _ = dictionary.apply_text("Ретро прошло. ретро было полезным.", [Entry("ретроспектива", ("ретро",))])
    assert text == "Ретроспектива прошло. ретроспектива было полезным."


def test_already_correct_text_is_not_counted():
    assert dictionary.apply_text("КХД и Kafka уже верно", [Entry("КХД"), Entry("Kafka")]) == ("КХД и Kafka уже верно", 0)


def test_hint_and_result():
    entries = [Entry("КХД", ("кахэде",)), Entry("Kafka")]
    assert dictionary.hint(entries) == "Термины: КХД, Kafka."
    assert dictionary.hint([]) == ""
    result = {"text": "в кахэде", "segments": [{"start": 0, "end": 1, "text": " в кахэде"}, {"start": 1, "end": 2, "text": "ок"}]}
    fixed, n = dictionary.apply_result(result, entries)
    assert n == 1 and fixed["segments"][0]["text"] == " в КХД" and fixed["text"] == "в КХД ок"
    assert result["segments"][0]["text"] == " в кахэде"  # исходный результат не изменён


def test_apply_transcript_keeps_stamps_and_speakers():
    body = "\n[00:00:01] **Кахэде:** грузим в кахэде\n[00:00:05] без метки кахэде\nне реплика кахэде\n"
    fixed, n = dictionary.apply_transcript(body, [Entry("КХД", ("кахэде",))])
    assert n == 2
    assert fixed == "\n[00:00:01] **Кахэде:** грузим в КХД\n[00:00:05] без метки КХД\nне реплика кахэде\n"


# --------------------------------------------------------------------------- словарь: API

def test_dictionary_api_round_trip_and_apply(client, items):
    original = client.get("/api/dictionary").json()
    saved = client.put("/api/dictionary", json={"text": "Альманах = альманах\n = плохая строка\n"})
    assert saved.status_code == 200
    body = saved.json()
    assert body["count"] == 1 and body["warnings"] == ["Строка 2: нет самого слова"]
    assert client.get("/api/dictionary").json()["text"].startswith("Альманах = альманах")
    assert client.put("/api/dictionary", json={"text": 5}).status_code == 400
    assert client.put("/api/dictionary", json={"text": "я" * 200_000}).status_code == 400

    result = client.post("/api/dictionary/apply").json()
    assert result["meetings"] >= 1 and result["replacements"] >= 1
    found = [m for m in client.get("/api/meetings", params={"q": "Альманах"}).json()["items"]]
    detail = client.get(f"/api/meetings/{found[0]['id']}").json()
    assert any("Альманах" in seg["text"] for seg in detail["transcript"])
    assert client.post("/api/dictionary/apply").json()["replacements"] == 0  # повтор ничего не меняет

    # возвращаем фикстуру в исходное состояние
    client.put("/api/dictionary", json={"text": "альманах = Альманах\n"})
    client.post("/api/dictionary/apply")
    client.put("/api/dictionary", json={"text": original["text"]})
    assert client.post("/api/dictionary/apply").json()["replacements"] == 0


def test_empty_dictionary_apply_is_harmless(client):
    saved = client.get("/api/dictionary").json()
    client.put("/api/dictionary", json={"text": ""})
    assert client.post("/api/dictionary/apply").json()["replacements"] == 0
    client.put("/api/dictionary", json={"text": saved["text"]})


def test_jobs_pass_dictionary_path_to_worker():
    import inspect
    from memory_desk import jobs
    assert "PLAUD_DICTIONARY" in inspect.getsource(jobs.start)


# --------------------------------------------------------------------------- уведомление

def test_notification_after_processing(client, monkeypatch):
    monkeypatch.setenv("MEMORY_DESK_FAKE_APPLE", "1")
    watcher = client.app.state.watcher
    core = client.app.state.core
    apple.sent_notifications.clear()
    before = {m.id for m in core.meetings}
    watcher._notify_done(before)
    assert apple.sent_notifications == []  # ничего нового — тишина

    tasks = storage.rows("SELECT id, meeting_id, status FROM tasks ORDER BY meeting_id, id")
    target = tasks[0]["meeting_id"]
    with storage.connect() as db:  # два черновика у одной записи
        for row in [t for t in tasks if t["meeting_id"] == target][:2]:
            db.execute("UPDATE tasks SET status='draft' WHERE id=?", (row["id"],))
    watcher._notify_done(before - {target})
    title, message = apple.sent_notifications[-1]
    assert title and message.startswith("Запись готова, проверьте ") and "задач" in message
    drafts = storage.one("SELECT COUNT(*) n FROM tasks WHERE status='draft' AND meeting_id=?", (target,))["n"]
    assert message.endswith(f"{drafts} задачи" if drafts in (2, 3, 4) else f"{drafts} задач" if drafts != 1 else "1 задачу")

    apple.sent_notifications.clear()
    cfg = dict(core.cfg)
    core.cfg["notify"] = False
    try:
        watcher._notify_done(before - {target})
    finally:
        core.cfg.update(cfg)
    assert apple.sent_notifications == []


def test_applescript_text_is_escaped():
    assert apple._applescript_text('Он сказал "стоп" \\ и ушёл') == "Он сказал 'стоп' и ушёл"
    assert len(apple._applescript_text("я" * 500)) == 200


def test_notify_is_noop_off_macos(monkeypatch):
    monkeypatch.delenv("MEMORY_DESK_FAKE_APPLE", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    assert apple.notify("a", "b") is False


# --------------------------------------------------------------------------- резервные копии по расписанию

def test_backup_schedule_and_pruning(client):
    watcher = client.app.state.watcher
    core = client.app.state.core
    saved = dict(core.cfg)
    try:
        for path in storage.list_backups():
            path.unlink()
        core.cfg.update(backup_auto=False)
        watcher._last_backup_check = 0
        assert watcher.backup_if_due() is False and storage.list_backups() == []

        core.cfg.update(backup_auto=True, backup_interval_hours=6, backup_keep=3)
        watcher._last_backup_check = 0
        assert watcher.backup_if_due() is True and len(storage.list_backups()) == 1
        # свежая копия есть — новая не нужна, даже если проверка наступила
        assert watcher.backup_if_due(now=time.time() + 700) is False and len(storage.list_backups()) == 1
        # прошло больше интервала — копия появляется
        assert watcher.backup_if_due(now=time.time() + 6 * 3600 + 60) is True and len(storage.list_backups()) == 2
        # лишние старые удаляются
        for _ in range(5):
            storage.create_backup()
        assert len(storage.list_backups()) == 7
        core.cfg.update(backup_keep=3)
        watcher._last_backup_check = 0
        watcher.backup_if_due()
        assert len(storage.list_backups()) == 3
    finally:
        core.cfg.update(saved)


def test_backup_settings_are_validated_and_saved(client):
    cfg = client.get("/api/settings").json()
    base = {k: cfg[k] for k in ("recordings_dir", "obsidian_dir", "archive_dir")}
    ok = client.put("/api/settings", json={**base, "backup_interval_hours": 0, "backup_keep": 999, "notify": False}).json()
    assert ok["backup_interval_hours"] == 1 and ok["backup_keep"] == 60 and ok["notify"] is False
    ok = client.put("/api/settings", json={**base, "backup_interval_hours": 12, "backup_keep": 5, "notify": True}).json()
    assert (ok["backup_interval_hours"], ok["backup_keep"], ok["notify"], ok["backup_auto"]) == (12, 5, True, True)
    client.put("/api/settings", json={**base})  # возврат значений по умолчанию
    assert client.get("/api/settings").json()["backup_keep"] == 7


def test_storage_backups_follow_keep_setting(client):
    cfg = client.get("/api/settings").json()
    base = {k: cfg[k] for k in ("recordings_dir", "obsidian_dir", "archive_dir")}
    client.put("/api/settings", json={**base, "backup_keep": 2})
    try:
        for _ in range(4):
            storage.create_backup()
        cats = {c["key"]: c for c in client.get("/api/storage").json()["categories"]}
        assert "2" in cats["backups"]["hint"] and cats["backups"]["clean"] == "Оставить 2"
        client.post("/api/storage/clean", json={"category": "backups"})
        assert len(storage.list_backups()) == 2
    finally:
        client.put("/api/settings", json={**base})


# --------------------------------------------------------------------------- этап 0: скрипт проверки

def test_stage0_sweep_and_masking():
    import importlib.util
    from pathlib import Path
    import numpy as np
    spec = importlib.util.spec_from_file_location("stage0_report", Path(__file__).resolve().parents[1] / "scripts" / "stage0_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rng = np.random.default_rng(3)
    centers = [np.eye(16)[0], np.eye(16)[1]]
    embeddings = []
    for i in range(24):
        vector = centers[i % 2] + 0.05 * rng.normal(size=16)
        embeddings.append(vector / np.linalg.norm(vector))
    rows = module.sweep_cluster(embeddings, [8.0] * 24, expected=2)
    assert [r["sim"] for r in rows][0] == 0.3 and any(r["match"] for r in rows)
    assert all(r["speakers"] >= 1 for r in rows)
    assert "/Users/someone" not in module.mask(str(Path.home()) + "/x")
