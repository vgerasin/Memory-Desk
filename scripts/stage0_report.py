#!/usr/bin/env python3
"""Этап 0: проверка Memory Desk на реальном Mac. Собирает один отчёт без текстов записей и личных данных.

    .venv/bin/python scripts/stage0_report.py                       # система, компоненты, доступы, 10 запусков модели
    .venv/bin/python scripts/stage0_report.py --models qwen3:4b,qwen3:8b
    .venv/bin/python scripts/stage0_report.py --voice запись.m4a --speakers 2

В отчёт попадают версии, числа и тайминги. Имена файлов, пути из домашней папки, содержание стенограмм и ответы модели
в него не попадают. Отчёт сохраняется в stage0-report.md — его можно прислать целиком.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE = """[00:00:03] Коллеги, давайте начнём. Сегодня обсуждаем перенос витрины продаж на инкрементальную загрузку.
[00:00:12] Сейчас полная перезагрузка занимает больше четырёх часов, и аналитики жалуются.
[00:00:25] Предлагаю перейти на инкрементальную загрузку, а слой сырых данных до конца квартала не трогать.
[00:00:40] Согласен, сырые данные не меняем. Анна, ты подготовишь план миграции до пятнадцатого ноября?
[00:00:52] Да, план подготовлю. Но мне нужен доступ к тестовому контуру.
[00:01:05] Доступ выдам сегодня, это моя задача. Остаётся вопрос, где хранить историю изменений справочников.
[00:01:20] Пока не решили, вернёмся к этому на следующей встрече. Ещё риск: если источник начнёт присылать дубли, загрузка сломается.
[00:01:38] Хорошо, тогда фиксируем: инкрементальная загрузка принята, историю справочников обсудим позже."""


# --------------------------------------------------------------------------- вспомогательное

class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def h(self, title: str) -> None:
        self.lines += ["", f"## {title}", ""]
        print(f"\n== {title}")

    def row(self, name: str, value: str, state: str = "") -> None:
        mark = {"ok": "✅", "warn": "⚠️", "bad": "❌"}.get(state, "•")
        self.lines.append(f"- {mark} **{name}:** {value}")
        print(f"  {mark} {name}: {value}")

    def text(self, value: str) -> None:
        self.lines.append(value)
        print("  " + value.replace("\n", "\n  "))


def mask(text: str) -> str:
    home = str(Path.home())
    return str(text).replace(home, "~").replace(os.environ.get("USER", "\0"), "<user>")


def sh(*command: str, timeout: int = 15) -> str:
    try:
        return subprocess.run(list(command), capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def guarded(report: Report, title: str, function, *args) -> None:
    report.h(title)
    try:
        function(report, *args)
    except Exception as exc:  # noqa: BLE001 — сбой одной проверки не должен прятать остальные
        report.row("Проверка не завершилась", mask(f"{type(exc).__name__}: {exc}"), "bad")
        report.lines.append("```\n" + mask(traceback.format_exc(limit=3)) + "```")


# --------------------------------------------------------------------------- 1. система

def check_system(report: Report) -> None:
    from memory_desk import __version__
    mem = sh("sysctl", "-n", "hw.memsize")
    gib = round(int(mem) / 1024 ** 3) if mem.isdigit() else None
    report.row("Memory Desk", __version__)
    report.row("Система", f"{platform.system()} {platform.mac_ver()[0] or platform.release()}, {platform.machine()}")
    report.row("Процессор", sh("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor() or "не определён",
               "ok" if platform.machine() == "arm64" else "warn")
    report.row("Память", f"{gib} ГБ" if gib else "не определена", "ok" if gib and gib >= 16 else "warn")
    report.row("Python", platform.python_version())
    report.row("Свободно на диске", f"{shutil.disk_usage(ROOT).free / 1024 ** 3:.0f} ГБ",
               "ok" if shutil.disk_usage(ROOT).free > 20 * 1024 ** 3 else "warn")
    report.row("Виртуальное окружение .venv", "есть" if (ROOT / ".venv/bin/python").exists() else "нет (установщик не запускался?)",
               "ok" if (ROOT / ".venv/bin/python").exists() else "warn")


def check_components(report: Report) -> None:
    from memory_desk import config
    ffmpeg = shutil.which("ffmpeg")
    report.row("ffmpeg", mask(ffmpeg) if ffmpeg else "не найден", "ok" if ffmpeg else "bad")
    for module, label in (("mlx_whisper", "mlx-whisper"), ("qdrant_client", "qdrant-client"), ("fastapi", "fastapi"),
                          ("torch", "torch (для голосов)"), ("speechbrain", "speechbrain (для голосов)"),
                          ("EventKit", "pyobjc-framework-EventKit"),
                          ("Vision", "pyobjc-framework-Vision (текст с фото)")):
        found = importlib.util.find_spec(module) is not None
        needed = module in ("mlx_whisper", "qdrant_client", "fastapi")
        report.row(label, "установлен" if found else "нет", "ok" if found else ("bad" if needed else "warn"))
    cache = config.MODEL_CACHE
    cached = any(cache.glob("**/*whisper*")) if cache.exists() else False
    report.row("Модель Whisper в кэше", "есть" if cached else "ещё не скачана (скачается при первой записи, нужен интернет)",
               "ok" if cached else "warn")
    version = ollama_get("/api/version")
    if version is None:
        report.row("Ollama", f"не отвечает на {config.OLLAMA_URL}", "bad")
    else:
        report.row("Ollama", f"работает, версия {version.get('version', '?')}", "ok")
        tags = ollama_get("/api/tags") or {}
        names = [m.get("name", "") for m in tags.get("models", [])]
        report.row("Модели Ollama", ", ".join(names) or "нет", "ok" if names else "warn")
        report.row(f"Рабочая модель {config.OLLAMA_MODEL}", "скачана" if any(n.startswith(config.OLLAMA_MODEL) for n in names) else "не найдена",
                   "ok" if any(n.startswith(config.OLLAMA_MODEL) for n in names) else "bad")


def ollama_get(path: str) -> dict | None:
    from memory_desk import config
    try:
        with urllib.request.urlopen(config.OLLAMA_URL + path, timeout=5) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def check_apple(report: Report) -> None:
    from memory_desk import apple
    status = apple.backend().status()
    if not status.get("available"):
        report.row("Календарь и Напоминания", status.get("reason", "недоступно"), "bad")
        return
    for key, label in (("events", "Календарь"), ("reminders", "Напоминания")):
        value = status.get(key)
        report.row(label, str(value), "ok" if value == "authorized" else "warn")
    if status.get("events") != "authorized" or status.get("reminders") != "authorized":
        report.text("Доступ выдаётся в приложении: Настройки → «Разрешить доступ». Затем запустите проверку ещё раз.")


def check_ocr(report: Report) -> None:
    """Распознавание текста на фото: рисуем картинку с известным русским и английским текстом и смотрим, что прочтёт движок."""
    import tempfile
    from PIL import Image, ImageDraw, ImageFont
    from memory_desk import ocr
    engines = ocr.engines()
    report.row("Движки распознавания", ", ".join(engines) or "нет (Vision не установлен, Tesseract не найден)", "ok" if engines else "warn")
    if not engines:
        report.text("На macOS Vision ставится установщиком (pyobjc-framework-Vision); запасной вариант: brew install tesseract tesseract-lang.")
        return
    font = ImageFont.truetype(str(ROOT / "memory_desk/fonts/DejaVuSans.ttf"), 44)
    lines = ["План запуска проекта", "Срок: пятнадцатое ноября", "Ответственный: Анна", "Project launch plan"]
    image = Image.new("RGB", (1200, 120 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((40, 30 + i * 100), line, fill="black", font=font)
    with tempfile.TemporaryDirectory(prefix="md-ocr-check-") as tmp:
        path = Path(tmp) / "check.png"
        image.save(path)
        started = time.time()
        result = ocr.recognize(path)
        seconds = time.time() - started
    low = result.text.lower()
    russian = sum(word in low for word in ("запуска", "проекта", "пятнадцатое", "ноября", "ответственный", "анна"))
    english = "launch" in low and "plan" in low
    report.row(f"Распознавание ({result.engine})", f"русский: {russian} из 6 слов, английский: {'да' if english else 'нет'}, {seconds:.1f} с",
               "ok" if russian >= 5 and english else ("warn" if russian >= 3 or english else "bad"))
    if result.note:
        report.text(result.note)


# --------------------------------------------------------------------------- 2. устойчивость JSON модели

def work_transcripts(limit: int) -> list[str]:
    """Готовые расшифровки из work/ (по одной на запись), новые первыми. Тексты остаются на компьютере."""
    import meetings_process as mp
    found = []
    for path in sorted((ROOT / "work").glob("*/transcript.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            text = mp.transcript_markdown(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if len(text) > 300:
            found.append(text)
        if len(found) >= limit:
            break
    return found


def run_model(report: Report, model: str, texts: list[str], runs: int, synthetic: bool) -> dict:
    import meetings_process as mp
    calls: list[dict] = []
    real = mp.ollama_json

    def counting(prompt, **kwargs):
        kwargs["attempts"] = 1  # считаем именно первую попытку; в работе их до трёх
        started = time.time()
        try:
            data = real(prompt, **kwargs)
            calls.append({"ok": True, "sec": time.time() - started})
            return data
        except RuntimeError as exc:
            calls.append({"ok": False, "sec": time.time() - started, "offline": "не отвечает" in str(exc)})
            raise

    mp.ollama_json, mp.OLLAMA_MODEL = counting, model
    outcomes = []
    try:
        for index in range(runs):
            text = texts[index % len(texts)]
            before = len(calls)
            started = time.time()
            try:
                data = mp.normalize_analysis(mp.analyze(text), text)
                outcomes.append({"ok": True, "sec": time.time() - started, "calls": len(calls) - before,
                                 "tasks": len(data.get("tasks", [])), "decisions": len(data.get("decisions", []))})
            except RuntimeError as exc:
                offline = "не отвечает" in str(exc)
                outcomes.append({"ok": False, "sec": time.time() - started, "calls": len(calls) - before, "offline": offline})
                if offline:
                    break
            print(f"    {model}: {index + 1}/{runs}", flush=True)
    finally:
        mp.ollama_json = real
    return {"calls": calls, "runs": outcomes}


def check_ollama(report: Report, models: list[str], runs: int) -> None:
    if ollama_get("/api/version") is None:
        report.row("Ollama недоступна", "запустите приложение Ollama и повторите", "bad")
        return
    texts = work_transcripts(runs)
    synthetic = not texts
    if synthetic:
        texts = [SAMPLE]
        report.text(f"Готовых расшифровок в work/ нет — взят встроенный вымышленный текст ({runs} запусков). "
                    "Для честной оценки сначала обработайте записи и повторите.")
    else:
        report.text(f"Расшифровок из work/: {len(texts)} (тексты в отчёт не попадают). Запусков на модель: {runs}.")
    for model in models:
        result = run_model(report, model, texts, runs, synthetic)
        calls, outcomes = result["calls"], result["runs"]
        if outcomes and outcomes[-1].get("offline"):
            report.row(model, "Ollama не отвечает или модель не загружена", "bad")
            continue
        failed = sum(1 for c in calls if not c["ok"])
        total = len(calls)
        share = failed / total if total else 0
        report.row(f"{model}: запросов к модели", f"{total}, с некорректным JSON с первой попытки: {failed} ({share:.0%})",
                   "ok" if share <= 0.05 else "warn" if share <= 0.2 else "bad")
        done = [o for o in outcomes if o["ok"]]
        report.row(f"{model}: записей без ошибки", f"{len(done)} из {len(outcomes)} (с одной попыткой на запрос)",
                   "ok" if len(done) == len(outcomes) else "warn")
        if done:
            seconds = [o["sec"] for o in done]
            report.row(f"{model}: время на запись", f"среднее {statistics.mean(seconds):.0f} с, медиана {statistics.median(seconds):.0f} с, "
                       f"максимум {max(seconds):.0f} с")
            report.row(f"{model}: найдено задач / решений", f"в среднем {statistics.mean(o['tasks'] for o in done):.1f} / "
                       f"{statistics.mean(o['decisions'] for o in done):.1f} на запись")
    report.text("В работе приложение повторяет запрос до трёх раз, поэтому ошибка на записи возможна, только если подряд не удались "
                "все попытки. Если доля первой попытки выше 5–10 %, стоит усилить формат запроса.")


# --------------------------------------------------------------------------- 3. голоса

def sweep_cluster(embeddings, seconds, expected: int | None) -> list[dict]:
    """Сколько голосов находится при разных CLUSTER_SIM. Чистая функция, проверяется тестами."""
    from memory_desk import voice
    out = []
    for sim in [round(0.30 + 0.05 * i, 2) for i in range(0, 9)]:
        groups = voice.cluster(embeddings, seconds, sim=sim)
        out.append({"sim": sim, "speakers": len(set(groups))})
    if expected:
        for row in out:
            row["match"] = row["speakers"] == expected
    return out


def check_voice(report: Report, audio: str, expected: int | None) -> None:
    import meetings_process as mp
    from memory_desk import voice
    ok, reason = voice.available()
    if not ok:
        report.row("Голоса недоступны", reason, "bad")
        return
    path = Path(audio).expanduser()
    if not path.exists():
        report.row("Файл для проверки голосов", "не найден", "bad")
        return
    work = ROOT / "work" / "_stage0"
    work.mkdir(parents=True, exist_ok=True)
    normalized = work / "voice.wav"
    started = time.time()
    mp.normalize_audio(path, normalized)
    result = mp.clean_result(mp.transcribe(normalized))[0]
    segments = result.get("segments", [])
    report.row("Расшифровка", f"{len(segments)} реплик за {time.time() - started:.0f} с")
    audio_data = voice.load_audio(normalized)
    spans = voice.segment_spans(segments, len(audio_data) / voice.SAMPLE_RATE)
    started = time.time()
    embeddings = voice.segment_embeddings(audio_data, spans, voice.get_encoder())
    seconds = [max(0.0, e - s) for s, e in spans]
    have = [e for e in embeddings if e is not None]
    report.row("Отпечатки голоса", f"{len(have)} из {len(embeddings)} реплик за {time.time() - started:.0f} с")
    sweep = sweep_cluster(embeddings, seconds, expected)
    report.row("Текущее значение CLUSTER_SIM", str(voice.CLUSTER_SIM))
    report.lines.append("")
    report.lines.append("| CLUSTER_SIM | найдено голосов |" + (" совпало |" if expected else ""))
    report.lines.append("|---|---|" + ("---|" if expected else ""))
    for row in sweep:
        report.lines.append(f"| {row['sim']} | {row['speakers']} |" + (f" {'да' if row['match'] else ''} |" if expected else ""))
        print(f"  CLUSTER_SIM {row['sim']}: голосов {row['speakers']}" + (" ←" if expected and row["match"] else ""))
    if expected:
        good = [r["sim"] for r in sweep if r["match"]]
        report.row("Значения, дающие ожидаемые голоса", ", ".join(map(str, good)) if good else "ни одно — модель путает голоса",
                   "ok" if good else "bad")
        if good:
            report.text(f"Рекомендация: CLUSTER_SIM = {statistics.median(good)} (середина подходящего диапазона).")
    groups = voice.cluster(embeddings, seconds)
    cents = voice.centroids_by_group(embeddings, seconds, groups)
    overlaps = voice.find_overlaps(embeddings, groups, cents)
    share = len(overlaps) / len(embeddings) if embeddings else 0
    report.row("Пометки «наложение голосов?»", f"{len(overlaps)} из {len(embeddings)} реплик ({share:.0%}); предел OVERLAP_SHARE = {voice.OVERLAP_SHARE:.0%}")
    if overlaps:
        stamps = ", ".join(f"{int(spans[i][0]) // 60}:{int(spans[i][0]) % 60:02d}" for i in overlaps[:20])
        report.text("Времена пометок (проверьте на слух, что в них действительно перебивают друг друга): " + stamps)
    report.text("Узнавание людей: добавьте голоса в «Люди», обработайте запись «По голосу» и проверьте, что узнаны верные люди, "
                "а посторонние остались «Спикер N». Если путаются похожие голоса — поднимайте MATCH_THRESHOLD "
                f"(сейчас {voice.MATCH_THRESHOLD}); если знакомых не узнаёт — снижайте.")
    shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- запуск

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=10, help="сколько анализов выполнить на каждую модель (по умолчанию 10)")
    parser.add_argument("--models", default="", help="модели Ollama через запятую (по умолчанию рабочая из настроек)")
    parser.add_argument("--skip-ollama", action="store_true")
    parser.add_argument("--voice", metavar="ФАЙЛ", help="запись с несколькими голосами для подбора порогов")
    parser.add_argument("--speakers", type=int, help="сколько людей говорит в этой записи")
    parser.add_argument("--out", default=str(ROOT / "stage0-report.md"))
    args = parser.parse_args()

    from memory_desk import config
    models = [m.strip() for m in args.models.split(",") if m.strip()] or [config.OLLAMA_MODEL]
    report = Report()
    report.lines.append("# Memory Desk: отчёт этапа 0")
    report.lines.append(f"\nСоздан {time.strftime('%Y-%m-%d %H:%M')}. Тексты записей и имена файлов в отчёт не входят.")
    guarded(report, "Система", check_system)
    guarded(report, "Компоненты", check_components)
    guarded(report, "Календарь и Напоминания", check_apple)
    guarded(report, "Распознавание текста на фото", check_ocr)
    if not args.skip_ollama:
        guarded(report, "Устойчивость JSON и скорость модели", check_ollama, models, max(1, args.runs))
    if args.voice:
        guarded(report, "Голоса: пороги и наложение", check_voice, args.voice, args.speakers)
    out = Path(args.out)
    out.write_text("\n".join(report.lines) + "\n", encoding="utf-8")
    print(f"\nОтчёт сохранён: {mask(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
