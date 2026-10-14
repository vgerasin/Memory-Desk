"""Этап 13: качество распознавания речи — подсказка, подготовка звука, слабые места, предложения в словарь, замер."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import memory_desk.asr, memory_desk.asr_bench, memory_desk.config, memory_desk.dictionary  # noqa: F401
    import memory_desk.jobs, memory_desk.knowledge, memory_desk.services  # noqa: F401
    globals().update(asr=memory_desk.asr, bench=memory_desk.asr_bench, config=memory_desk.config,
                     dictionary=memory_desk.dictionary, jobs=memory_desk.jobs, knowledge=memory_desk.knowledge,
                     services=memory_desk.services)


# --------------------------------------------------------------------------- модель и звук

def test_resolve_model():
    assert asr.resolve_model("turbo").endswith("large-v3-turbo")
    assert asr.resolve_model("large-v3").endswith("whisper-large-v3-mlx")
    assert asr.resolve_model("автор/модель") == asr.resolve_model("")        # кириллица в имени репозитория не принимается
    assert asr.resolve_model("some-org/whisper-ru-mlx") == "some-org/whisper-ru-mlx"
    assert asr.resolve_model("../../etc/passwd") == asr.resolve_model("turbo")
    assert asr.valid_model("turbo") and asr.valid_model("a/b") and not asr.valid_model("a b") and not asr.valid_model("")


def test_audio_filter_modes():
    assert asr.audio_filter("off") == "" and asr.audio_filter("nonsense") == ""
    assert "loudnorm" in asr.audio_filter("light") and "afftdn" not in asr.audio_filter("light")
    assert "afftdn" in asr.audio_filter("strong")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")
@pytest.mark.parametrize("mode", ["light", "strong"])
def test_audio_filter_is_valid_for_ffmpeg(tmp_path, mode):
    """Цепочка фильтров принимается настоящим ffmpeg и даёт непустой файл 16 кГц."""
    src, out = tmp_path / "tone.wav", tmp_path / "out.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-af", asr.audio_filter(mode),
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)], check=True)
    assert out.stat().st_size > 10_000


def test_normalize_audio_command(monkeypatch, tmp_path):
    import meetings_process as mp
    seen = []
    monkeypatch.setattr(mp, "run", lambda command: seen.append(command))
    mp.normalize_audio(tmp_path / "a.mp3", tmp_path / "w" / "a.wav")
    mp.normalize_audio(tmp_path / "a.mp3", tmp_path / "w" / "a.wav", "light")
    assert "-af" not in seen[0] and seen[0][-1].endswith("a.wav")
    assert seen[1][seen[1].index("-af") + 1] == asr.audio_filter("light")


def test_slug_is_shared_with_pipeline():
    import meetings_process as mp
    for text in ("PLAUD 2026-10-12 10-00", "a/b:c*d", "Очень " * 40, "", "  .. "):
        assert mp.slug(text) == asr.slug(text)


# --------------------------------------------------------------------------- подсказка

def test_build_prompt_orders_and_limits():
    prompt = asr.build_prompt(["КХД", "Kafka"], ["Иван", "Мария"], ["Альфа"])
    assert prompt == "Термины: КХД, Kafka. Участники: Иван, Мария. Проекты: Альфа."
    assert asr.build_prompt([], [], []) == ""
    assert asr.build_prompt([], ["Иван"], []) == "Участники: Иван."
    long_terms = [f"Термин{i:03d}" for i in range(200)]
    prompt = asr.build_prompt(long_terms, ["Иван"], ["Альфа"])
    assert len(prompt) <= asr.PROMPT_CHARS and "Иван" in prompt and "Альфа" in prompt   # словарь не вытесняет имена
    assert asr.build_prompt(["a", "a", " a "]) == "Термины: a."


def test_context_names_orders_by_meetings():
    ctx = asr.context_names("Валерий Герасин", [{"name": "Анна", "meetings": 1}, {"name": "Борис", "meetings": 5}, {"name": ""}],
                            [{"name": "Бета", "meetings": 1}, {"name": "Альфа", "meetings": 3}])
    assert ctx["people"] == ["Валерий", "Борис", "Анна"] and ctx["projects"] == ["Альфа", "Бета"]


def test_read_context_tolerates_garbage(tmp_path):
    assert asr.read_context(None) == {"people": [], "projects": []}
    bad = tmp_path / "c.json"
    bad.write_text("{не json", encoding="utf-8")
    assert asr.read_context(bad) == {"people": [], "projects": []}
    bad.write_text(json.dumps({"people": ["А", 5, None], "projects": "x"}), encoding="utf-8")
    assert asr.read_context(bad) == {"people": ["А"], "projects": []}


def test_pipeline_prompt_respects_switch(monkeypatch, tmp_path):
    import meetings_process as mp
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({"people": ["Иван"], "projects": ["Альфа"]}), encoding="utf-8")
    terms = [dictionary.Entry("КХД")]
    monkeypatch.setenv("PLAUD_ASR_CONTEXT", str(ctx))
    monkeypatch.setenv("PLAUD_ASR_HINT", "1")
    assert "Иван" in mp.asr_prompt(terms) and "Альфа" in mp.asr_prompt(terms)
    monkeypatch.setenv("PLAUD_ASR_HINT", "0")
    assert mp.asr_prompt(terms) == dictionary.hint(terms) and "Иван" not in mp.asr_prompt(terms)


def test_jobs_start_passes_asr_settings(monkeypatch, tmp_path, client):
    core = client.app.state.core
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "run")
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")
    audio = tmp_path / "rec.mp3"
    audio.write_bytes(b"x")
    captured = {}

    class Fake:
        pid = 987654

        def __init__(self, command, **kwargs):
            captured.update(kwargs)

        def poll(self):
            return 0

    monkeypatch.setattr(jobs.subprocess, "Popen", Fake)
    cfg = {**core.cfg, "asr_model": "large-v3", "asr_hint": False, "asr_prep": "light", "my_name": "Валерий Иванов"}
    try:
        result = jobs.start(cfg, [audio])
    finally:
        jobs._children.pop(987654, None)
    assert result["created"]
    env = captured["env"]
    assert env["PLAUD_WHISPER_MODEL"] == "large-v3" and env["PLAUD_ASR_HINT"] == "0" and env["PLAUD_ASR_PREP"] == "light"
    context = json.loads(Path(env["PLAUD_ASR_CONTEXT"]).read_text(encoding="utf-8"))
    assert context["people"][0] == "Валерий"


# --------------------------------------------------------------------------- слабые места

def seg(start, text="слово", logprob=-0.2, compression=1.5, no_speech=0.0):
    return {"start": start, "text": text, "avg_logprob": logprob, "compression_ratio": compression, "no_speech_prob": no_speech}


def test_weak_spots_flags_doubtful_segments():
    result = {"segments": [seg(1), seg(10, logprob=-0.9), seg(20), seg(30, compression=2.5), seg(40), seg(50),
                           seg(60, logprob=-0.6, no_speech=0.5), seg(70), seg(80), seg(90, text=" ", logprob=-2)]}
    assert asr.weak_spots(result) == [10, 30, 60]


def test_weak_spots_never_flags_most_of_the_text():
    result = {"segments": [seg(i, logprob=-0.8 - i / 100) for i in range(10)]}
    spots = asr.weak_spots(result)
    assert len(spots) == 4 and spots == [6, 7, 8, 9]              # худшие 40 %
    assert asr.weak_spots({"segments": []}) == [] and asr.weak_spots({}) == []


def test_weak_format_roundtrip():
    assert asr.parse_weak(asr.format_weak([3, 95, 301])) == {3, 95, 301}
    assert asr.parse_weak(None) == set() and asr.parse_weak("") == set()


def test_render_transcript_has_weak_spots_line():
    import meetings_process as mp
    from datetime import datetime
    args = (Path("a.mp3"), datetime(2026, 10, 1, 10, 0), 60, "T", "n.md", "[00:00:01] текст")
    assert 'weak_spots: "5 70"' in mp.render_transcript(*args, weak=[5, 70])
    assert "weak_spots" not in mp.render_transcript(*args)
    meta, _ = knowledge.parse_frontmatter(mp.render_transcript(*args, weak=[5, 70]))
    assert asr.parse_weak(meta["weak_spots"]) == {5, 70}


def test_meeting_detail_marks_weak_lines(client, monkeypatch):
    item = next(m for m in client.get("/api/meetings", params={"limit": 1000}).json()["items"]
                if client.get(f"/api/meetings/{m['id']}").json()["transcript"])
    lines = client.get(f"/api/meetings/{item['id']}").json()["transcript"]
    assert not any(l.get("weak") for l in lines)
    monkeypatch.setattr(knowledge, "transcript_weak", lambda _m: {int(lines[0]["sec"])})
    flagged = client.get(f"/api/meetings/{item['id']}").json()["transcript"]
    assert flagged[0].get("weak") is True and not any(l.get("weak") for l in flagged[1:] if int(l["sec"]) != int(lines[0]["sec"]))


# --------------------------------------------------------------------------- правки → словарь

def test_edits_between_finds_word_replacements():
    assert asr.edits_between("Обсудили кахэде и кафка", "Обсудили КХД и Kafka") == [("кахэде", "КХД"), ("кафка", "Kafka")]
    assert asr.edits_between("проверить кхд завтра", "проверить КХД завтра") == [("кхд", "КХД")]
    assert asr.edits_between("Иван пришёл", "иван пришёл") == []                 # просто регистр обычного слова — не правка
    assert asr.edits_between("привет, мир", "привет мир!") == []                 # пунктуация — не правка
    assert asr.edits_between("раз два три четыре пять", "один шесть семь восемь девять") == []   # переписана вся фраза


def test_suggest_filters_noise_and_known():
    pairs = {"m1": [("кахэде", "КХД"), ("да", "нет"), ("1", "2"), ("прогрес", "прогресс")],
             "m2": [("кахэде", "КХД"), ("кафка", "Kafka")]}
    out = asr.suggest_from_edits(pairs, known={("кафка", "kafka")}, dismissed=set())
    assert [(r["wrong"], r["right"], r["count"]) for r in out] == [("кахэде", "КХД", 2), ("прогрес", "прогресс", 1)]
    assert out[0]["meetings"] == ["m1", "m2"]
    out = asr.suggest_from_edits(pairs, known=set(), dismissed={asr.pair_key("кахэде", "КХД")})
    assert all(r["right"] != "КХД" for r in out)


def test_add_variant_keeps_file_structure():
    text = "# заметка\nКХД = кахэде\nKafka"
    assert dictionary.add_variant(text, "КХД", "ка ха дэ") == "# заметка\nКХД = кахэде, ка ха дэ\nKafka"
    assert dictionary.add_variant(text, "Kafka", "кафка") == "# заметка\nКХД = кахэде\nKafka = кафка"
    assert dictionary.add_variant(text, "Новое", "нов").endswith("\nНовое = нов")
    assert dictionary.add_variant(text, "КХД", "Кахэде") == text                  # такой вариант уже есть
    assert dictionary.add_variant("", "КХД", "кхд") == "КХД"                        # регистр: вариант не нужен


@pytest.fixture()
def edited_meeting(client, monkeypatch, tmp_path):
    """Запись, у которой «исходное» распознавание отличается от стенограммы одной опечаткой."""
    monkeypatch.setenv("PLAUD_DICTIONARY", str(tmp_path / "dictionary.txt"))
    monkeypatch.setattr(jobs, "_paths", lambda: {"work": tmp_path / "work"})
    core = client.app.state.core
    for meeting in core.meetings:
        if not meeting.source_audio or not knowledge.transcript_file(meeting):
            continue
        for seg_ in knowledge.transcript_segments(meeting):
            word = next((w for w in seg_["text"].split() if len(w) >= 6 and w.isalpha()), None)
            if word:
                break
        else:
            continue
        typo = word[:-1] + ("к" if word[-1] != "к" else "т")
        original = tmp_path / "work" / asr.slug(Path(meeting.source_audio).stem) / "transcript.md"
        original.parent.mkdir(parents=True)
        original.write_text("\n\n".join(f"[{s['t']}] " + (s["text"].replace(word, typo, 1) if s["i"] == seg_["i"] else s["text"])
                                        for s in knowledge.transcript_segments(meeting)) + "\n", encoding="utf-8")
        services._EDIT_CACHE.clear()
        return meeting, typo, word
    pytest.skip("в тестовых данных нет записи со стенограммой и исходным файлом")


def test_suggestions_from_edits_accept_and_dismiss(client, edited_meeting, tmp_path):
    meeting, typo, word = edited_meeting
    found = client.get("/api/dictionary/suggestions").json()["items"]
    mine = [r for r in found if r["wrong"] == typo.casefold() and r["right"] == word.casefold()] or \
           [r for r in found if r["wrong"] == typo.casefold()]
    assert mine and mine[0]["count"] >= 1 and meeting.title in mine[0]["meetings"]
    right = mine[0]["right"]
    done = client.post("/api/dictionary/suggestions/accept", json={"wrong": typo.casefold(), "right": right}).json()
    assert right in done["text"] and typo.casefold() in done["text"]
    again = client.get("/api/dictionary/suggestions").json()["items"]
    assert not any(r["wrong"] == typo.casefold() for r in again)             # принятое больше не предлагается
    # отклонение запоминается в файле рядом со словарём
    client.post("/api/dictionary/suggestions/dismiss", json={"wrong": "абвгд", "right": "абвгде"})
    assert asr.pair_key("абвгд", "абвгде") in json.loads((tmp_path / "dictionary-dismissed.json").read_text(encoding="utf-8"))


def test_no_original_means_no_suggestions(client, monkeypatch, tmp_path):
    monkeypatch.setenv("PLAUD_DICTIONARY", str(tmp_path / "dictionary.txt"))
    monkeypatch.setattr(jobs, "_paths", lambda: {"work": tmp_path / "nothing"})
    services._EDIT_CACHE.clear()
    assert client.get("/api/dictionary/suggestions").json() == {"items": []}


# --------------------------------------------------------------------------- настройки

def put_settings(client, **changes):
    current = client.get("/api/settings").json()
    current.pop("exists", None)
    return client.put("/api/settings", json={**current, **changes})


def test_asr_settings_roundtrip_and_validation(client):
    original = {k: client.get("/api/settings").json()[k] for k in ("asr_model", "asr_hint", "asr_prep")}
    assert original == {"asr_model": "turbo", "asr_hint": True, "asr_prep": "off"}
    try:
        assert put_settings(client, asr_model="large-v3", asr_hint=False, asr_prep="strong").status_code == 200
        saved = client.get("/api/settings").json()
        assert (saved["asr_model"], saved["asr_hint"], saved["asr_prep"]) == ("large-v3", False, "strong")
        assert put_settings(client, asr_model="кириллица").status_code == 400
        assert put_settings(client, asr_prep="max").status_code == 400
        assert put_settings(client, asr_model="org/whisper-ru-mlx").status_code == 200
    finally:
        assert put_settings(client, **original).status_code == 200


# --------------------------------------------------------------------------- замер

def test_wer_cer_and_normalization():
    ref = "Сегодня обсудили КХД, и Kafka. Ёлка!"
    assert bench.normalize("[00:00:05] **Иван:** Ёлка, Привет!") == ["елка", "привет"]
    assert bench.wer(ref, ref) == 0 and bench.cer(ref, ref) == 0
    assert bench.wer("раз два три четыре", "раз два три") == pytest.approx(0.25)
    assert bench.wer("раз два", "раз три") == pytest.approx(0.5)
    assert bench.wer("", "") == 0 and bench.wer("", "слово") == 1
    assert 0 < bench.cer("привет", "привем") < 0.2


def test_term_recall_and_report():
    total, found = bench.term_recall("обсудили КХД и Kafka, потом снова КХД", "обсудили кахэде и кафка потом снова кхд", ["КХД", "Kafka", ""])
    assert (total, found) == (3, 1)
    good, bad = bench.Run("хорошая"), bench.Run("плохая")
    good.add("a", "раз два три четыре", "раз два три четыре", 10, 100, [])
    bad.add("a", "раз два три четыре", "раз два", 20, 100, [])
    table = bench.report([bad, good])
    rows = table.splitlines()
    assert rows[2].startswith("| хорошая | 0.0 %") and "×10.0" in rows[2] and "50.0 %" in rows[3]
    assert good.speed == 10 and bad.speed == 5


def test_benchmark_script_scores_ready_transcripts(tmp_path):
    folder = tmp_path / "eta"
    (folder / "ready").mkdir(parents=True)
    (folder / "a.txt").write_text("Сегодня обсудили КХД и Kafka", encoding="utf-8")
    (folder / "ready" / "a.md").write_text("[00:00:01] **Иван:** сегодня обсудили КХД и кафка", encoding="utf-8")
    out = tmp_path / "o.json"
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "asr_benchmark.py"), str(folder), "--hypotheses",
                           str(folder / "ready"), "--out", str(out)], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0]["wer"] == pytest.approx(0.2) and "| готовые стенограммы |" in done.stdout
    empty = subprocess.run([sys.executable, str(ROOT / "scripts" / "asr_benchmark.py"), str(tmp_path / "ready")],
                           capture_output=True, text=True, cwd=ROOT)
    assert empty.returncode == 1
