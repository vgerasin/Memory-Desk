"""Словарь терминов: слова, которые распознавание путает (аббревиатуры, названия, имена).

Файл обычный текстовый, по строке на термин:

    КХД = кахэде, ка ха дэ
    Философт = философ, фило софт
    Kafka
    # строка с решёткой — комментарий

Слева — как слово должно писаться, справа через запятую — как его ошибочно пишет Whisper. Строка без «=»
просто сообщает модели слово и выправляет регистр («кхд» → «КХД»). Файл читается при каждой обработке записи,
поэтому правка действует сразу, без перезапуска.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 100_000
MAX_ENTRIES = 1000
HINT_CHARS = 400  # подсказка Whisper ограничена ~224 токенами; держим запас

_SPLIT = re.compile(r"\s*[,;]\s*")


@dataclass(frozen=True)
class Entry:
    term: str
    variants: tuple[str, ...] = ()


def path() -> Path:
    override = os.environ.get("PLAUD_DICTIONARY")
    if override:
        return Path(override)
    from . import config
    return config.DATA_DIR / "dictionary.txt"


def parse(text: str) -> tuple[list[Entry], list[str]]:
    """Возвращает (записи, предупреждения о пропущенных строках)."""
    entries: list[Entry] = []
    warnings: list[str] = []
    seen: dict[str, int] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        term, sep, rest = line.partition("=")
        term = " ".join(term.split())
        if not term:
            warnings.append(f"Строка {number}: нет самого слова")
            continue
        if len(term) > 80:
            warnings.append(f"Строка {number}: слово длиннее 80 символов")
            continue
        variants = [" ".join(v.split()) for v in _SPLIT.split(rest.strip()) if v.strip()] if sep else []
        variants = [v for v in variants if v and v.casefold() != term.casefold() and len(v) <= 80]
        key = term.casefold()
        if key in seen:
            old = entries[seen[key]]
            merged = tuple(dict.fromkeys(old.variants + tuple(variants)))
            entries[seen[key]] = Entry(old.term, merged)
            continue
        if len(entries) >= MAX_ENTRIES:
            warnings.append(f"Слов больше {MAX_ENTRIES}: остальные пропущены")
            break
        seen[key] = len(entries)
        entries.append(Entry(term, tuple(dict.fromkeys(variants))))
    return entries, warnings


def read_text() -> str:
    target = path()
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return ""


def load() -> list[Entry]:
    return parse(read_text())[0]


def save_text(text: str) -> tuple[list[Entry], list[str]]:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Словарь слишком большой: не больше 100 КБ")
    entries, warnings = parse(text)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(text.rstrip() + "\n" if text.strip() else "", encoding="utf-8")
    temp.replace(target)
    return entries, warnings


def hint(entries: list[Entry]) -> str:
    """Короткая подсказка для Whisper: сами слова, без вариантов."""
    words: list[str] = []
    total = 0
    for entry in entries:
        if total + len(entry.term) + 2 > HINT_CHARS:
            break
        words.append(entry.term)
        total += len(entry.term) + 2
    return ("Термины: " + ", ".join(words) + ".") if words else ""


def _has_upper(term: str) -> bool:
    return any(ch.isupper() for ch in term)


def _pattern(entries: list[Entry]):
    """Один шаблон на все варианты, чтобы замены не цеплялись друг за друга. → (regex, {ключ: Entry})."""
    table: dict[str, Entry] = {}
    for entry in entries:
        for variant in entry.variants:
            table.setdefault(_key(variant), entry)
        if _has_upper(entry.term):  # «кхд» → «КХД»; обычные слова в нижнем регистре не трогаем
            table.setdefault(_key(entry.term), entry)
    if not table:
        return None, table
    words = sorted(table, key=len, reverse=True)
    parts = [r"\s+".join(re.escape(piece) for piece in word.split(" ")) for word in words]
    regex = re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)
    return regex, table


def _key(text: str) -> str:
    return " ".join(text.casefold().split())


def apply_text(text: str, entries: list[Entry]) -> tuple[str, int]:
    regex, table = _pattern(entries)
    if regex is None or not text:
        return text, 0
    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        entry = table.get(_key(match.group(0)))
        if entry is None:
            return match.group(0)
        out = entry.term
        # слово в начале предложения остаётся с заглавной, если термин написан строчными
        if match.group(0)[:1].isupper() and not _has_upper(out):
            out = out[:1].upper() + out[1:]
        if out != match.group(0):
            count += 1
        return out

    return regex.sub(repl, text), count


def apply_result(result: dict, entries: list[Entry]) -> tuple[dict, int]:
    """Правит реплики результата Whisper; возвращает (результат, число замен)."""
    total = 0
    segments = []
    for segment in result.get("segments", []):
        text, n = apply_text(str(segment.get("text", "")), entries)
        total += n
        segments.append({**segment, "text": text} if n else segment)
    fixed = {**result, "segments": segments}
    if total:
        fixed["text"] = " ".join(str(s.get("text", "")).strip() for s in segments if str(s.get("text", "")).strip())
    return fixed, total


def apply_transcript(body: str, entries: list[Entry]) -> tuple[str, int]:
    """Правит готовую стенограмму `[мм:сс] **Говорящий:** текст`: таймкоды и метки говорящих не трогает."""
    from . import diarize
    if not entries:
        return body, 0
    total = 0
    out: list[str] = []
    for raw in body.split("\n"):
        match = diarize.TS_LINE.match(raw.strip())
        if not match:
            out.append(raw)
            continue
        stamp, text = match.groups()
        marked = diarize.SPEAKER.match(text)
        prefix, speech = ("**" + marked.group(1).strip() + ":** ", marked.group(2)) if marked else ("", text)
        fixed, count = apply_text(speech, entries)
        if not count:
            out.append(raw)
            continue
        total += count
        out.append(f"[{stamp}] {prefix}{fixed}")
    return "\n".join(out), total


# --------------------------------------------------------------------------- предложения из правок

def dismissed_path() -> Path:
    return path().with_name("dictionary-dismissed.json")


def dismissed() -> set[str]:
    try:
        raw = json.loads(dismissed_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(x) for x in raw} if isinstance(raw, list) else set()


def dismiss(key: str) -> None:
    items = sorted(dismissed() | {key})[-2000:]
    target = dismissed_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    temp.replace(target)


def add_variant(text: str, term: str, variant: str) -> str:
    """Добавляет в текст словаря «term = variant»: к существующей строке слова или отдельной строкой. Комментарии не трогает."""
    term, variant = " ".join(term.split()), " ".join(variant.split())
    if not term:
        raise ValueError("Нет самого слова")
    lines = text.splitlines()
    for number, raw in enumerate(lines):
        head, sep, rest = raw.partition("=")
        if raw.strip().startswith("#") or head.strip().casefold() != term.casefold():
            continue
        if not variant or variant.casefold() == term.casefold():
            return text
        have = [v.casefold() for v in _SPLIT.split(rest.strip()) if v.strip()] if sep else []
        if variant.casefold() in have:
            return text
        lines[number] = f"{head.strip()} = {rest.strip()}, {variant}" if sep and rest.strip() else f"{head.strip()} = {variant}"
        return "\n".join(lines)
    new = term if not variant or variant.casefold() == term.casefold() else f"{term} = {variant}"
    return "\n".join([*lines, new]) if text.strip() else new
