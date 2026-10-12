"""Фильтр «галлюцинаций» Whisper: на музыке, шуме и тишине модель выдумывает текст — зацикленные фразы,
«Субтитры сделал…», бессмысленные повторы. Чистим результат до того, как он попадёт в стенограмму и анализ."""
from __future__ import annotations

import re
import zlib

NO_SPEECH = 0.6          # вероятность «речи нет» выше этого вместе с низкой уверенностью — тишина или музыка
LOW_LOGPROB = -1.0
COMPRESSION = 2.4        # сильно сжимаемый текст — зацикливание (порог самого Whisper)
MAX_RUN = 2              # столько одинаковых реплик подряд оставляем, остальные — повтор

JUNK_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"^субтитры\s+(сделал|создал|подготовил|делал)\b.*",
    r"^редактор\s+субтитров\b.*",
    r"^корректор\b.*",
    r"^продолжение\s+следует\W*$",
    r"^спасибо\s+за\s+(просмотр|внимание)\W*$",
    r"^подписывайтесь\s+на\s+(мой\s+|наш\s+)?канал\W*$",
    r"^(ставьте\s+лайки.*)$",
    r"^thanks?\s+for\s+watching\W*$",
    r"^(dimatorzok|dima\s*torzok)\W*$",
    r"^\W*(музыка|аплодисменты|смех)\W*$",
    r"^\[?(музыка|music|applause)\]?\W*$",
)]
LOOP = re.compile(r"(.{2,60}?)(?:[\s,.;:!?…-]+\1){3,}", re.IGNORECASE | re.DOTALL)


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def compression_ratio(text: str) -> float:
    raw = text.encode("utf-8")
    return len(raw) / max(1, len(zlib.compress(raw)))


def collapse_loops(text: str) -> str:
    """«Да да да да да да» → «Да». Одинаковый фрагмент, повторённый 4 и более раз подряд, остаётся один раз."""
    return LOOP.sub(lambda m: m.group(1).strip(), text).strip()


def reason(segment: dict, text: str) -> str | None:
    if not _norm(text):
        return "пусто"
    if any(p.match(text.strip()) for p in JUNK_PATTERNS):
        return "служебная фраза"
    if float(segment.get("no_speech_prob", 0) or 0) > NO_SPEECH and float(segment.get("avg_logprob", 0) or 0) < LOW_LOGPROB:
        return "нет речи"
    if len(text) > 40 and (float(segment.get("compression_ratio", 0) or 0) > COMPRESSION or compression_ratio(text) > 3.0):
        return "зацикливание"
    return None


def clean_result(result: dict) -> tuple[dict, list[dict]]:
    """Возвращает (результат без мусорных реплик, список отброшенных с причинами)."""
    kept: list[dict] = []
    dropped: list[dict] = []
    run_text, run = "", 0
    for segment in result.get("segments", []):
        text = str(segment.get("text", "")).strip()
        why = reason(segment, text)
        if not why:
            text = collapse_loops(text)
            key = _norm(text)
            run = run + 1 if key == run_text else 1
            run_text = key
            if run > MAX_RUN:
                why = "повтор"
        if why:
            dropped.append({"start": segment.get("start"), "text": text[:120], "reason": why})
            continue
        kept.append({**segment, "text": text})
    cleaned = {**result, "segments": kept}
    cleaned["text"] = " ".join(s["text"] for s in kept)
    return cleaned, dropped
