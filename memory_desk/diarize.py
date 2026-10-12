"""Говорящие в стенограмме: оценка локальной моделью и перезапись меток `**Имя:** реплика`.

Без анализа голоса: модель по тексту оценивает смену говорящего (вопрос — ответ, обращения, реакции, паузы).
Результат — гипотеза, которую человек поправляет в интерфейсе, поэтому ошибки не фатальны.
"""
from __future__ import annotations

import re
from typing import Callable

TS_LINE = re.compile(r"^\[(\d{1,2}:\d{2}:\d{2})\]\s*(.*)$")
SPEAKER = re.compile(r"^\*\*([^*:]{1,40}):\*\*\s*(.*)$")
GENERIC = re.compile(r"^Спикер \d+$")
LETTERS = "ABCDEFGH"
WINDOW = 40          # реплик в одном запросе
CONTEXT = 4          # размеченных реплик из предыдущего окна для связки
MIN_GAP = 1.5        # паузы короче не показываем модели


def to_seconds(stamp: str) -> int:
    parts = [int(x) for x in stamp.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse(body: str) -> list[dict]:
    """Реплики в порядке следования. `i` — порядковый номер строки с таймкодом (не зависит от пустых реплик)."""
    segments: list[dict] = []
    for raw in body.splitlines():
        line = raw.strip()
        match = TS_LINE.match(line)
        if not match:
            if segments and line:
                segments[-1]["text"] = (segments[-1]["text"] + " " + line).strip()
            continue
        stamp, text = match.groups()
        speaker = ""
        marked = SPEAKER.match(text)
        if marked:
            speaker, text = marked.group(1).strip(), marked.group(2)
        segments.append({"i": len(segments), "t": stamp, "sec": to_seconds(stamp), "speaker": speaker, "text": text.strip()})
    return segments


def relabel(body: str, labels: list[str]) -> str:
    """Подставляет метки по порядковому номеру реплики; пустая метка убирает разметку. Остальной текст не меняется."""
    out: list[str] = []
    index = -1
    for raw in body.splitlines():
        match = TS_LINE.match(raw.strip())
        if not match:
            out.append(raw)
            continue
        index += 1
        stamp, text = match.groups()
        marked = SPEAKER.match(text)
        if marked:
            text = marked.group(2)
        label = labels[index].strip() if index < len(labels) and labels[index] else ""
        label = re.sub(r"[*:\n]", "", label)[:40].strip()
        out.append(f"[{stamp}] **{label}:** {text}" if label else f"[{stamp}] {text}")
    return "\n".join(out) + ("\n" if body.endswith("\n") else "")


def _prompt(known: dict[str, str], context: list[tuple[int, str, str]], batch: list[dict], first: int) -> str:
    lines = []
    for number, seg in zip(range(first, first + len(batch)), batch):
        gap = seg.get("gap")
        pause = f" (пауза {gap:.1f} с)" if isinstance(gap, (int, float)) and gap >= MIN_GAP else ""
        lines.append(f"[{number}]{pause} {seg['text'].strip()[:400]}")
    roster = ""
    if known:
        roster = "Уже известные говорящие (последняя реплика каждого):\n" + "\n".join(
            f"{k}: «{v[:120]}»" for k, v in known.items()) + "\n\n"
    ctx = ""
    if context:
        ctx = "Предыдущие реплики (уже размечены):\n" + "\n".join(f"[{n}] {who}: {text[:200]}" for n, who, text in context) + "\n\n"
    return f"""Ты размечаешь стенограмму разговора по говорящим. Имён в тексте нет — определи смену говорящего по смыслу:
вопрос и ответ на него, обращение к собеседнику, возражение или согласие, короткие реакции («угу», «да», «ну»), смена позиции, паузы между репликами.
Реплики одного человека идут подряд. Говорящих обозначай буквами A, B, C… Используй как можно меньше говорящих: нового добавляй, только если реплика явно принадлежит другому человеку.
Если в записи говорит один человек (лекция, монолог, диктовка), используй только A.

{roster}{ctx}Новые реплики (номер в квадратных скобках):
{chr(10).join(lines)}

Верни только JSON: {{"turns": [{{"from": номер реплики, "speaker": "A"}}]}}.
Каждый элемент означает: начиная с этой реплики и до следующего элемента говорит указанный человек. Первый элемент — с реплики {first}.""".strip()


def _apply_turns(data: dict, first: int, count: int, known_letters: set[str], previous: str) -> list[str]:
    turns = data.get("turns")
    result: list[str] = []
    marks: dict[int, str] = {}
    if isinstance(turns, list):
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            try:
                start = int(turn.get("from"))
            except (TypeError, ValueError):
                continue
            letter = str(turn.get("speaker") or "").strip().upper()[:1]
            if letter in LETTERS and first <= start < first + count:
                marks[start] = letter
    current = previous
    for number in range(first, first + count):
        if number in marks:
            current = marks[number]
        result.append(current)
    return result


def detect_speakers(
    segments: list[dict], chat: Callable[[str], dict], progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Возвращает метки «Спикер N» для каждой реплики. Пустой список — говорящий один или оценка не удалась."""
    total = len(segments)
    if total < 4:
        return []
    letters: list[str] = []
    known: dict[str, str] = {}
    previous = "A"
    windows = (total + WINDOW - 1) // WINDOW
    for w in range(windows):
        start = w * WINDOW
        batch = segments[start:start + WINDOW]
        context = [(start + n, letters[start + n], segments[start + n]["text"]) for n in range(max(0, start - CONTEXT), start)]
        data = chat(_prompt(known, context, batch, start))
        part = _apply_turns(data, start, len(batch), set(known), previous)
        letters.extend(part)
        previous = part[-1]
        for n, letter in zip(range(start, start + len(batch)), part):
            known[letter] = segments[n]["text"]
        if progress:
            progress(w + 1, windows)
    order: dict[str, str] = {}
    for letter in letters:
        order.setdefault(letter, f"Спикер {len(order) + 1}")
    if len(order) < 2:
        return []
    return [order[letter] for letter in letters]
