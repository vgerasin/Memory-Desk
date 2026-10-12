"""Говорящие в записях: фоновая оценка моделью для уже готовых записей и привязка к людям."""
from __future__ import annotations

import re
import threading
import time

from . import config, diarize, knowledge, llm, storage, voice

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def make_chat():
    """Функция запроса к модели; вынесена, чтобы её можно было подменить в тестах."""
    bad = config.LOGS_DIR / "ollama-last-bad-response.txt"
    return lambda prompt: llm.ollama_json(prompt, model=config.OLLAMA_MODEL, url=config.OLLAMA_URL, bad_response_path=bad)


def status(meeting_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(meeting_id)
        return dict(job) if job else None


def start(core, meeting: knowledge.Meeting, mode: str = "text") -> bool:
    """Запускает оценку в фоне. mode: text — по тексту (Ollama), voice — по голосу. False — если уже идёт."""
    with _lock:
        if _jobs.get(meeting.id, {}).get("state") == "running":
            return False
        _jobs[meeting.id] = {"state": "running", "mode": mode, "done": 0, "total": 0, "message": "", "startedAt": time.time()}
    threading.Thread(target=_run, args=(core, meeting, mode), name="diarize", daemon=True).start()
    return True


def _update(meeting_id: str, **values) -> None:
    with _lock:
        _jobs.setdefault(meeting_id, {}).update(values)


def _run_voice(core, meeting: knowledge.Meeting, segments: list[dict]) -> None:
    audio = knowledge.meeting_audio(meeting)
    if not audio:
        raise RuntimeError("Аудиофайл не найден: возможно, он удалён по сроку хранения. Определите говорящих по тексту")
    ok, reason = voice.available()
    if not ok:
        raise RuntimeError(reason)
    result = voice.diarize_by_voice(audio, segments, profiles=voice.load_profiles(),
                                    progress=lambda done, total: _update(meeting.id, done=done, total=total))
    labels = result["labels"]
    if not labels:
        _update(meeting.id, state="done", message="По голосу в записи слышен один человек: разметка не добавлена")
        return
    knowledge.rewrite_speakers(meeting, lambda _parsed: labels)
    voice.save_meeting_voices(meeting.id, result["speakers"])
    link_people(meeting.id, labels)
    core.reload(force=True)
    known = [l for l, info in result["speakers"].items() if info["matched"]]
    text = f"Найдено говорящих: {len(set(labels))}"
    if known:
        text += f", узнано по голосу: {', '.join(known)}"
    _update(meeting.id, state="done", message=text)


def _run(core, meeting: knowledge.Meeting, mode: str = "text") -> None:
    try:
        path, heading = knowledge._transcript_target(meeting)
        _head, _sep, body = path.read_text(encoding="utf-8").partition(heading)
        segments = diarize.parse(body)
        if len(segments) < 4:
            _update(meeting.id, state="done", message="Стенограмма слишком короткая для разметки")
            return
        if mode == "voice":
            _run_voice(core, meeting, segments)
            return
        labels = diarize.detect_speakers(
            segments, make_chat(), lambda done, total: _update(meeting.id, done=done, total=total))
        if not labels:
            _update(meeting.id, state="done", message="Модель считает, что говорит один человек: разметка не добавлена")
            return
        knowledge.rewrite_speakers(meeting, lambda _parsed: labels)
        core.reload(force=True)
        _update(meeting.id, state="done", message=f"Найдено говорящих: {len(set(labels))}")
    except Exception as exc:  # noqa: BLE001 — любая причина должна дойти до интерфейса
        text = str(exc)
        if "не отвечает" not in text and "неразборчив" not in text:
            text = f"Не удалось определить говорящих: {text}"
        _update(meeting.id, state="error", message=text)


# --------------------------------------------------------------------------- люди

def canonical_name(name: str) -> str:
    """Имя человека, как оно уже записано в справочнике (по полному имени или единственному совпадению имени)."""
    clean = " ".join(name.split())
    folded = clean.casefold()
    people = storage.rows("SELECT name FROM people")
    for row in people:
        if " ".join(row["name"].split()).casefold() == folded:
            return row["name"]
    tokens = sorted(folded.replace(",", " ").split())
    if len(tokens) > 1:  # «Соколова Анна» и «Анна Соколова» — один человек
        same = [row["name"] for row in people if sorted(row["name"].casefold().replace(",", " ").split()) == tokens]
        if len(same) == 1:
            return same[0]
    first = [row["name"] for row in people if row["name"].split() and row["name"].split()[0].casefold() == folded]
    return first[0] if len(first) == 1 else clean


def is_generic(label: str) -> bool:
    return bool(diarize.GENERIC.match(label.strip()))


def link_people(meeting_id: str, labels: list[str]) -> None:
    """Добавляет названных людей в участники записи (никого не удаляя)."""
    names = {l for l in labels if l and not is_generic(l)}
    if names:
        storage.add_meeting_people(meeting_id, sorted(names))


def summarize(segments: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for seg in segments:
        if seg["speaker"]:
            counts[seg["speaker"]] = counts.get(seg["speaker"], 0) + 1
    return [{"label": label, "count": count, "generic": is_generic(label)}
            for label, count in sorted(counts.items(), key=lambda kv: -kv[1])]


def clean_label(value: str) -> str:
    return re.sub(r"[*:\n\r]", "", " ".join(str(value).split()))[:40].strip()
