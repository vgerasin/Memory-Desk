"""Этап 14: правка стенограммы в кабинете, выбор модели анализа, MCP и командная строка."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import memory_desk.audit, memory_desk.knowledge  # noqa: F401
    globals().update(audit=memory_desk.audit, knowledge=memory_desk.knowledge)


# --------------------------------------------------------------------------- правка реплики

TRANSCRIPT = """---
title: "Тест"
weak_spots: "5 40"
---

# Стенограмма

## Текст с таймкодами

[00:00:05] **Иван:** Обсудили кахэде и сроки.
продолжение той же реплики

[00:00:20] **Анна:** Согласна.

[00:00:40] без метки говорящего
"""


@pytest.fixture()
def fake_note(tmp_path):
    folder = tmp_path / "Транскрипты"
    folder.mkdir()
    (folder / "т.md").write_text(TRANSCRIPT, encoding="utf-8")
    note = tmp_path / "заметка.md"
    note.write_text("# Тест\n\n[[Транскрипты/т.md]]\n", encoding="utf-8")
    return SimpleNamespace(path=note, content=note.read_text(encoding="utf-8")), folder / "т.md"


def test_edit_changes_only_that_line(fake_note):
    meeting, transcript = fake_note
    done = knowledge.edit_segment(meeting, 0, "  Обсудили КХД   и сроки. ", was="Обсудили кахэде и сроки. продолжение той же реплики")
    assert done == {"i": 0, "sec": 5, "old": "Обсудили кахэде и сроки. продолжение той же реплики", "text": "Обсудили КХД и сроки.", "changed": True}
    after = transcript.read_text(encoding="utf-8")
    assert "[00:00:05] **Иван:** Обсудили КХД и сроки.\n\n[00:00:20] **Анна:** Согласна." in after
    assert "продолжение той же реплики" not in after
    assert after.replace('weak_spots: "40"\n', 'weak_spots: "5 40"\n').count("[00:00:20]") == 1
    assert 'weak_spots: "40"' in after                      # секунда 5 исправлена и больше не «сомнительная»
    segments = knowledge.diarize.parse(after.partition("## Текст с таймкодами")[2])
    assert [(s["t"], s["speaker"]) for s in segments] == [("00:00:05", "Иван"), ("00:00:20", "Анна"), ("00:00:40", "")]


def test_edit_without_speaker_and_last_weak_spot(fake_note):
    meeting, transcript = fake_note
    knowledge.edit_segment(meeting, 2, "Без метки, но поправлено")
    knowledge.edit_segment(meeting, 0, "Раз")
    after = transcript.read_text(encoding="utf-8")
    assert "[00:00:40] Без метки, но поправлено" in after and "weak_spots" not in after   # пустую строку не оставляем


def test_edit_confirming_text_clears_weak_spot_without_changing_text(fake_note):
    meeting, transcript = fake_note
    done = knowledge.edit_segment(meeting, 1, "Согласна.", was="Согласна.")
    assert done["changed"] is False
    assert transcript.read_text(encoding="utf-8") == TRANSCRIPT       # для реплики без сомнений файл не меняется вовсе
    done = knowledge.edit_segment(meeting, 0, "Обсудили кахэде и сроки. продолжение той же реплики")
    assert done["changed"] is False and 'weak_spots: "40"' in transcript.read_text(encoding="utf-8")


@pytest.mark.parametrize("index, text, was, status", [
    (0, "   ", None, 400), (0, "я" * 4001, None, 400), (7, "текст", None, 404), (-1, "текст", None, 404),
    (1, "Новый текст", "Совсем другой текст", 409),
])
def test_edit_errors_leave_file_untouched(fake_note, index, text, was, status):
    meeting, transcript = fake_note
    with pytest.raises(knowledge.TranscriptEditError) as raised:
        knowledge.edit_segment(meeting, index, text, was)
    assert raised.value.status == status
    assert transcript.read_text(encoding="utf-8") == TRANSCRIPT


def test_edit_legacy_transcript_inside_the_note(tmp_path):
    note = tmp_path / "старая.md"
    text = "# Старая\n\n## Кратко\n- итог\n\n## Полная стенограмма\n\n[00:00:01] Привет.\n\n[00:00:09] Пока.\n"
    note.write_text(text, encoding="utf-8")
    meeting = SimpleNamespace(path=note, content=text)
    knowledge.edit_segment(meeting, 1, "До свидания.")
    assert note.read_text(encoding="utf-8") == text.replace("Пока.", "До свидания.")


def test_edit_missing_transcript(tmp_path):
    note = tmp_path / "без.md"
    note.write_text("# Без стенограммы\n", encoding="utf-8")
    with pytest.raises(knowledge.TranscriptEditError) as raised:
        knowledge.edit_segment(SimpleNamespace(path=note, content="# Без стенограммы\n"), 0, "текст")
    assert raised.value.status == 404


@pytest.fixture()
def real_meeting(client):
    """Запись из тестовых данных со стенограммой; файл возвращается в исходный вид после теста."""
    core = client.app.state.core
    for meeting in core.meetings:
        path = knowledge.transcript_file(meeting)
        if path and knowledge.transcript_segments(meeting):
            original = path.read_bytes()
            yield meeting, path
            path.write_bytes(original)
            core.reload(force=True)
            return
    pytest.skip("в тестовых данных нет записи со стенограммой")


def test_edit_api_round_trip_and_journal(client, real_meeting, monkeypatch, tmp_path):
    meeting, path = real_meeting
    monkeypatch.setattr(audit, "path", lambda: tmp_path / "audit.log")
    before = client.get(f"/api/meetings/{meeting.id}").json()["transcript"]
    first = before[0]
    r = client.patch(f"/api/meetings/{meeting.id}/transcript/{first['i']}", json={"text": "Совершенно новая реплика про КХД", "was": first["text"]})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "Совершенно новая реплика про КХД" and r.json()["old"] == " ".join(first["text"].split())
    after = client.get(f"/api/meetings/{meeting.id}").json()["transcript"]
    assert after[0]["text"] == "Совершенно новая реплика про КХД" and after[0]["t"] == first["t"] and after[0]["speaker"] == first["speaker"]
    assert [l["text"] for l in after[1:]] == [l["text"] for l in before[1:]]
    # правка попала в поиск по записи
    assert any(m["id"] == meeting.id for m in client.get("/api/meetings", params={"q": "Совершенно новая реплика"}).json()["items"])
    # откат тем же вызовом; устаревший текст отклоняется
    stale = client.patch(f"/api/meetings/{meeting.id}/transcript/{first['i']}", json={"text": "x", "was": first["text"]})
    assert stale.status_code == 409
    back = client.patch(f"/api/meetings/{meeting.id}/transcript/{first['i']}", json={"text": r.json()["old"], "was": r.json()["text"]})
    assert back.status_code == 200
    assert client.get(f"/api/meetings/{meeting.id}").json()["transcript"][0]["text"] == r.json()["old"]
    # журнал знает о правках, но не хранит текст
    entries = audit.query(actor="cabinet")["items"]
    edits = [e for e in entries if e["action"] == "record.transcript_edit" and e["result"] == "ok"]
    assert len(edits) == 2 and audit.labels()["record.transcript_edit"] == "Правка стенограммы"
    assert "Совершенно" not in (tmp_path / "audit.log").read_text(encoding="utf-8")


def test_edit_api_errors(client, real_meeting):
    meeting, _ = real_meeting
    assert client.patch("/api/meetings/нет-такой/transcript/0", json={"text": "x"}).status_code == 404
    assert client.patch(f"/api/meetings/{meeting.id}/transcript/9999", json={"text": "x"}).status_code == 404
    assert client.patch(f"/api/meetings/{meeting.id}/transcript/0", json={"text": "  "}).status_code == 400
    assert client.patch(f"/api/meetings/{meeting.id}/transcript/0", json={}).status_code == 422
