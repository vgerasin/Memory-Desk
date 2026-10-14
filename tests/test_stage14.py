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


# --------------------------------------------------------------------------- выбор модели анализа

@pytest.fixture()
def models(monkeypatch):
    """Подменяет Ollama: список скачанных моделей и пробный ответ; возвращает журнал запросов."""
    from memory_desk import llm
    state = {"installed": [{"name": "qwen3:4b", "size": 2_600_000_000, "params": "4.0B", "quant": "Q4_K_M"},
                           {"name": "llama3.1:8b", "size": 4_900_000_000, "params": "8.0B", "quant": "Q4_0"}],
             "asked": []}
    monkeypatch.setattr(llm, "list_models", lambda url, timeout=2.0: state["installed"])
    def fake_json(prompt, *, model, **kwargs):
        state["asked"].append(model)
        return {"ok": True}
    monkeypatch.setattr(llm, "ollama_json", fake_json)
    # модель в процессе сборки заметок не должна «прилипнуть» к другим тестам
    import meetings_process
    monkeypatch.setattr(meetings_process, "OLLAMA_MODEL", meetings_process.OLLAMA_MODEL)
    return state


def _settings(client, **changes):
    body = {k: v for k, v in client.get("/api/settings").json().items() if k != "exists"}
    return client.put("/api/settings", json={**body, **changes})


def test_llm_default_and_listing(client, models):
    from memory_desk import config
    data = client.get("/api/llm").json()
    assert data["default"] == config.DEFAULT_LLM == "qwen3:4b" and data["current"] == "qwen3:4b" and data["chosen"] == ""
    assert data["reachable"] and data["currentInstalled"]
    assert [m["name"] for m in data["installed"]] == ["qwen3:4b", "llama3.1:8b"]
    assert {s["name"]: s["installed"] for s in data["suggested"]} == {"qwen3:4b": True, "qwen3:8b": False}
    models["installed"] = None
    down = client.get("/api/llm").json()
    assert down["reachable"] is False and down["installed"] == [] and down["currentInstalled"] is False


def test_llm_choice_is_saved_and_used_everywhere(client, models):
    from memory_desk import config, conflicts, jobs, llm, services
    assert _settings(client, llm_model="llama3.1:8b").status_code == 200
    assert config.llm_model() == "llama3.1:8b" and client.get("/api/llm").json()["current"] == "llama3.1:8b"
    assert client.get("/api/settings").json()["llm_model"] == "llama3.1:8b"
    # серверные запросы к модели идут с выбранной моделью
    conflicts_call = llm.ollama_json("x", model=config.llm_model(), url=config.OLLAMA_URL)
    assert conflicts_call == {"ok": True} and models["asked"][-1] == "llama3.1:8b"
    # фоновая обработка получает её через окружение
    assert config.llm_model(client.app.state.core.cfg) == "llama3.1:8b"
    import inspect
    assert "PLAUD_OLLAMA_MODEL" in inspect.getsource(jobs.start)
    # статус системы называет выбранную модель
    assert services._ollama_status() == ("llama3.1:8b", "ok")
    # пустое значение возвращает модель по умолчанию
    assert _settings(client, llm_model="").status_code == 200 and config.llm_model() == config.DEFAULT_LLM
    assert services._ollama_status() == ("qwen3:4b", "ok")


def test_llm_status_when_model_is_missing(client, models):
    from memory_desk import services
    assert _settings(client, llm_model="qwen3:8b").status_code == 200
    assert services._ollama_status() == ("модель не загружена", "warn")
    _settings(client, llm_model="")


@pytest.mark.parametrize("name", ["bad name", "модель", "-x", "a;b", "x" * 200, "a b:c"])
def test_llm_name_is_validated(client, models, name):
    assert _settings(client, llm_model=name).status_code == 400
    assert client.post("/api/llm/check", json={"model": name}).status_code in (400, 422)


def test_llm_check_reports_answer_and_errors(client, models, monkeypatch):
    from memory_desk import llm
    ok = client.post("/api/llm/check", json={"model": "llama3.1:8b"}).json()
    assert ok["ok"] is True and ok["model"] == "llama3.1:8b" and models["asked"][-1] == "llama3.1:8b" and ok["seconds"] >= 0
    default = client.post("/api/llm/check", json={}).json()
    assert default["model"] == "qwen3:4b"

    def broken(prompt, **kwargs):
        raise RuntimeError("Модель «qwen3:8b» не скачана: выполните `ollama pull qwen3:8b` или выберите другую в настройках.")
    monkeypatch.setattr(llm, "ollama_json", broken)
    bad = client.post("/api/llm/check", json={"model": "qwen3:8b"}).json()
    assert bad["ok"] is False and "ollama pull qwen3:8b" in bad["message"]


def test_has_model_matches_exact_names_and_latest_tag():
    from memory_desk import llm
    installed = [{"name": "qwen3:4b"}, {"name": "mistral:latest"}, {"name": "hf.co/user/model:Q4_K_M"}]
    assert llm.has_model(installed, "qwen3:4b") and llm.has_model(installed, "mistral") and llm.has_model(installed, "hf.co/user/model:Q4_K_M")
    assert not llm.has_model(installed, "qwen3:8b") and not llm.has_model(installed, "qwen3") and not llm.has_model([], "mistral")


def test_ollama_404_names_the_missing_model(monkeypatch):
    import io, json, urllib.error
    from memory_desk import llm

    def refuse(request, timeout=None):
        raise urllib.error.HTTPError("http://x", 404, "not found", {}, io.BytesIO(json.dumps({"error": "model not found"}).encode()))
    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    with pytest.raises(RuntimeError, match="ollama pull qwen3:9b"):
        llm.ollama_json("x", model="qwen3:9b", url="http://x")


def test_worker_gets_model_from_environment(monkeypatch):
    import importlib
    monkeypatch.setenv("PLAUD_OLLAMA_MODEL", " qwen3:8b ")
    import meetings_process
    try:
        reloaded = importlib.reload(meetings_process)
        assert reloaded.OLLAMA_MODEL == "qwen3:8b"
    finally:
        monkeypatch.delenv("PLAUD_OLLAMA_MODEL")
        importlib.reload(meetings_process)
    assert meetings_process.OLLAMA_MODEL == "qwen3:4b"
