"""Качество распознавания речи: выбор модели, подсказка Whisper, подготовка звука, слабые места и предложения в словарь.

Модуль не зависит от Whisper и ffmpeg: всё, что здесь считается, проверяется тестами без моделей. Сам запуск
распознавания остаётся в `meetings_process.py`, а настройки приходят в него через переменные окружения (см. `jobs.start`).

Что здесь есть:
  * `resolve_model` — короткое имя модели («turbo», «large-v3») или полное имя на Hugging Face;
  * `build_prompt` — подсказка Whisper из словаря терминов, имён людей и названий проектов (сколько влезет);
  * `audio_filter` — цепочка фильтров ffmpeg для режимов подготовки звука «выкл», «лёгкая», «сильная»;
  * `weak_spots` — секунды, на которых модель сама сомневалась в тексте (по данным Whisper);
  * `suggest_from_edits` — какие слова вы поправили руками в стенограммах; из них получаются предложения в словарь.
"""
from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

DEFAULT_MODEL = "turbo"
MODELS = {
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
}
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}/[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

PREP_MODES = ("off", "light", "strong")
# Фильтры ffmpeg. «Лёгкая»: убираем гул ниже речи и выравниваем громкость. «Сильная»: ещё режем верх и подавляем
# ровный шум. Сильная подготовка может «съесть» тихие согласные, поэтому по умолчанию звук не трогаем:
# что лучше для ваших записей, показывает замер (scripts/asr_benchmark.py).
_FILTERS = {
    "light": "highpass=f=80,loudnorm=I=-18:TP=-2:LRA=11",
    "strong": "highpass=f=100,lowpass=f=7600,afftdn=nf=-25,loudnorm=I=-18:TP=-2:LRA=11",
}

PROMPT_CHARS = 400          # подсказка Whisper ограничена ~224 токенами; держим запас
TERMS_SHARE = 220           # из них словарю терминов — не больше этого
NAMES_SHARE = 120
WEAK_LOGPROB = -0.7         # средняя уверенность реплики ниже — «проверьте на слух»
WEAK_COMPRESSION = 2.2
WEAK_MAX_SHARE = 0.4        # подсвечиваем не больше 40 % реплик, иначе подсветка перестаёт что-либо значить


def resolve_model(value: object) -> str:
    """Имя модели → репозиторий на Hugging Face. Неизвестное значение заменяется моделью по умолчанию."""
    text = str(value or "").strip()
    if text in MODELS:
        return MODELS[text]
    if _REPO.match(text):
        return text
    return MODELS[DEFAULT_MODEL]


def valid_model(value: object) -> bool:
    text = str(value or "").strip()
    return text in MODELS or bool(_REPO.match(text))


def audio_filter(mode: object) -> str:
    """Цепочка фильтров для `ffmpeg -af`; пустая строка — звук не обрабатывается."""
    return _FILTERS.get(str(mode or "off"), "")


# --------------------------------------------------------------------------- подсказка Whisper

def _fit(label: str, items: list[str], budget: int) -> str:
    chosen: list[str] = []
    used = len(label) + 3
    for item in items:
        item = " ".join(str(item).split())
        if not item or item in chosen:
            continue
        if used + len(item) + 2 > budget:
            break
        chosen.append(item)
        used += len(item) + 2
    return f"{label}: " + ", ".join(chosen) + "." if chosen else ""


def build_prompt(terms: list[str], people: list[str] | None = None, projects: list[str] | None = None) -> str:
    """Подсказка для Whisper: «Термины: …. Участники: …. Проекты: ….»

    Сначала идёт словарь (его слова модель путает чаще всего), затем имена людей и названия проектов. Каждая часть
    ограничена своей долей, чтобы длинный словарь не вытеснил имена; общий предел — `PROMPT_CHARS`.
    """
    parts = [_fit("Термины", terms, TERMS_SHARE)]
    parts.append(_fit("Участники", people or [], NAMES_SHARE))
    left = PROMPT_CHARS - sum(len(p) + 1 for p in parts if p)
    parts.append(_fit("Проекты", projects or [], max(0, left)))
    return " ".join(p for p in parts if p)


def context_path_from_env() -> Path | None:
    value = os.environ.get("PLAUD_ASR_CONTEXT", "")
    return Path(value) if value else None


def read_context(path: Path | None) -> dict[str, list[str]]:
    """Имена и проекты, которые сервер положил рядом с заданием; нет файла или он испорчен — пустой контекст."""
    empty: dict[str, list[str]] = {"people": [], "projects": []}
    if not path:
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(raw, dict):
        return empty
    return {key: [v for v in raw.get(key, []) if isinstance(v, str)][:60] if isinstance(raw.get(key), list) else []
            for key in empty}


def context_names(my_name: str, people_rows: list[dict], project_rows: list[dict]) -> dict[str, list[str]]:
    """Список имён для подсказки: ваше имя, затем люди с самым большим числом встреч; проекты — по числу встреч.
    Каждый элемент — строка из базы; для людей берётся короткая форма, как её произносят («Иван», а не «Иван Петров»)."""
    people: list[str] = []
    if my_name.strip():
        people.append(my_name.strip().split()[0])
    for row in sorted(people_rows, key=lambda r: -int(r.get("meetings") or 0)):
        name = " ".join(str(row.get("name") or "").split())
        if name:
            people.append(name)
    projects = [" ".join(str(r.get("name") or "").split())
                for r in sorted(project_rows, key=lambda r: -int(r.get("meetings") or 0))]
    return {"people": list(dict.fromkeys(people))[:60], "projects": [p for p in dict.fromkeys(projects) if p][:30]}


# --------------------------------------------------------------------------- слабые места

def is_weak(segment: dict) -> bool:
    try:
        logprob = float(segment.get("avg_logprob", 0) or 0)
        compression = float(segment.get("compression_ratio", 0) or 0)
        no_speech = float(segment.get("no_speech_prob", 0) or 0)
    except (TypeError, ValueError):
        return False
    if not str(segment.get("text", "")).strip():
        return False
    return logprob < WEAK_LOGPROB or compression > WEAK_COMPRESSION or (logprob < -0.55 and no_speech > 0.3)


def weak_spots(result: dict) -> list[int]:
    """Секунды начала реплик, в тексте которых модель сомневалась. Если таких слишком много, остаются худшие."""
    segments = [s for s in result.get("segments", []) if str(s.get("text", "")).strip()]
    flagged = [s for s in segments if is_weak(s)]
    limit = int(len(segments) * WEAK_MAX_SHARE)
    if len(flagged) > limit:
        flagged = sorted(flagged, key=lambda s: float(s.get("avg_logprob", 0) or 0))[:limit]
    return sorted({int(float(s.get("start", 0) or 0)) for s in flagged})


def format_weak(seconds: list[int]) -> str:
    return " ".join(str(s) for s in seconds)


def parse_weak(value: object) -> set[int]:
    return {int(x) for x in re.findall(r"\d+", str(value or ""))}


# --------------------------------------------------------------------------- предложения в словарь из правок

WORD = re.compile(r"[\w'’-]+", re.UNICODE)
MAX_SPAN = 3                # правка длиннее трёх слов — это переписанная фраза, а не исправленное слово
MIN_SIMILARITY = 0.5        # слова после правки похожи на прежние (кроме терминов с заглавными и латиницей)


def _term_like(word: str) -> bool:
    return any(ch.isupper() for ch in word[1:]) or bool(re.search(r"[A-Za-z]", word)) or word.isupper()


def edits_between(original: str, edited: str) -> list[tuple[str, str]]:
    """Пары (было, стало) из одной реплики. Меняются 1–3 слова за раз; чистая пунктуация не считается правкой."""
    a, b = WORD.findall(original), WORD.findall(edited)
    pairs: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=[w.casefold() for w in a], b=[w.casefold() for w in b],
                                                       autojunk=False).get_opcodes():
        if tag == "equal":
            # то же слово, но исправлен регистр: «кхд» → «КХД»
            pairs += [(x, y) for x, y in zip(a[i1:i2], b[j1:j2]) if x != y and any(ch.isupper() for ch in y[1:] or y)]
        elif tag == "replace" and 1 <= i2 - i1 <= MAX_SPAN and 1 <= j2 - j1 <= MAX_SPAN:
            pairs.append((" ".join(a[i1:i2]), " ".join(b[j1:j2])))
    return pairs


def _acceptable(wrong: str, right: str) -> bool:
    if wrong.casefold() == right.casefold():
        return any(ch.isupper() for ch in right) and len(right) >= 2
    if len(wrong) < 3 or len(right) < 3 or wrong.isdigit() or right.isdigit():
        return False
    similar = difflib.SequenceMatcher(a=wrong.casefold(), b=right.casefold()).ratio() >= MIN_SIMILARITY
    return similar or _term_like(right)


def suggest_from_edits(pairs_by_meeting: dict[str, list[tuple[str, str]]], known: set[tuple[str, str]],
                       dismissed: set[str]) -> list[dict]:
    """Объединяет правки из всех записей. `known` — пары (слово, как верно), которые уже есть в словаре.
    Результат: [{wrong, right, count, meetings}] — чаще всего встречающиеся правки первыми."""
    table: dict[tuple[str, str], list[str]] = {}
    for meeting_id, pairs in pairs_by_meeting.items():
        for wrong, right in pairs:
            if not _acceptable(wrong, right):
                continue
            key = (wrong.casefold(), right)
            if (key[0], right.casefold()) in known or pair_key(wrong, right) in dismissed:
                continue
            table.setdefault(key, []).append(meeting_id)
    out = [{"wrong": wrong, "right": right, "count": len(ids), "meetings": list(dict.fromkeys(ids))[:5]}
           for (wrong, right), ids in table.items()]
    return sorted(out, key=lambda r: (-r["count"], r["right"].casefold(), r["wrong"]))[:50]


def pair_key(wrong: str, right: str) -> str:
    return f"{wrong.casefold()}=>{right}"


def known_pairs(entries) -> set[tuple[str, str]]:
    """Пары из словаря: (вариант, термин) и (термин, термин) для уже заведённых слов без вариантов."""
    out: set[tuple[str, str]] = set()
    for entry in entries:
        out.add((entry.term.casefold(), entry.term.casefold()))
        for variant in entry.variants:
            out.add((variant.casefold(), entry.term.casefold()))
    return out


# --------------------------------------------------------------------------- путь к рабочей папке задания

def slug(value: str, limit: int = 80) -> str:
    """Имя файла из заголовка и имя рабочей папки задания (`work/<slug>`): без запрещённых символов и переносов,
    не длиннее limit знаков (у файловой системы предел 255 байт на имя, а кириллица занимает по 2 байта)."""
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", " ".join(str(value).split())).strip(" .-")
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0].strip(" .-,;") if " " in cleaned[:limit] else cleaned[:limit]
    return cleaned or "Запись PLAUD"
