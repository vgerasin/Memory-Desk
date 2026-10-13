"""Закладки на записи: пометка важного места по таймкоду с коротким комментарием и цветом.

Закладки хранятся в самой заметке Obsidian, раздел `## Закладки`, по строке на закладку:
`- 🟡 `⏱ 00:12:30` — комментарий`. Заметка — единственный источник правды: потеря или откат базы SQLite их не
затрагивает, «Переиндексировать» только перечитывает заметки, а пересборка по шаблону переносит раздел как есть.
Место закладки — секунда аудио, поэтому правка стенограммы (замена слов, имена говорящих) её не сдвигает. Строки
раздела, которые не похожи на закладку (дописанные руками в Obsidian), при любой правке сохраняются как были.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from . import knowledge

TITLE = "Закладки"
COLORS = {"yellow": "🟡", "red": "🔴", "green": "🟢", "blue": "🔵", "purple": "🟣"}
DEFAULT_COLOR = "yellow"
MAX_TEXT = 300
MAX_PER_RECORD = 200
_BY_EMOJI = {v: k for k, v in COLORS.items()}
_LINE = re.compile(r"^-\s*(?P<emoji>" + "|".join(COLORS.values()) + r")?\s*`?⏱\s*(?P<t>\d{1,2}:\d{2}:\d{2})`?\s*(?:[—–-]\s*(?P<text>.*))?$")
_SECTION = re.compile(r"(?ms)^##[ \t]+" + TITLE + r"[ \t]*\n.*?(?=^##[ \t]+|\Z)")
_lock = threading.Lock()


class BookmarkError(RuntimeError):
    """Закладка не сохранена; текст понятен пользователю. `status` — код ответа API."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Parsed:
    items: list[dict]
    extras: list[str]   # строки раздела, которые не похожи на закладку: переносятся как есть


def stamp(sec: int) -> str:
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def parse_body(body: str) -> Parsed:
    items: dict[int, dict] = {}
    extras: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _LINE.match(line)
        sec = knowledge.to_seconds(match.group("t")) if match else None
        if not match or sec is None:
            extras.append(line)
            continue
        items.setdefault(sec, {"sec": sec, "t": stamp(sec), "text": " ".join((match.group("text") or "").split()),
                               "color": _BY_EMOJI.get(match.group("emoji") or "", DEFAULT_COLOR)})
    return Parsed(sorted(items.values(), key=lambda i: i["sec"]), extras)


def parse_content(content: str) -> Parsed:
    match = _SECTION.search(content)
    return parse_body(match.group(0).partition("\n")[2]) if match else Parsed([], [])


def list_for(meeting: knowledge.Meeting) -> list[dict]:
    return parse_content(meeting.content).items


def render(parsed: Parsed) -> str:
    lines = [f"- {COLORS[i['color']]} `⏱ {i['t']}`" + (f" — {i['text']}" if i["text"] else "") for i in parsed.items]
    return "## " + TITLE + "\n\n" + "\n".join(lines + parsed.extras).rstrip() + "\n"


def section_text(content: str) -> str:
    """Раздел «Закладки» как есть — чтобы пересборка заметки по шаблону его не теряла."""
    match = _SECTION.search(content)
    return match.group(0).rstrip() + "\n" if match else ""


def _write(meeting: knowledge.Meeting, text: str) -> None:
    temp = meeting.path.with_name(f".{meeting.path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(meeting.path)


def _place(text: str, section: str) -> str:
    """Раздел встаёт перед первым «Взглядом», а без них — перед «Стенограммой»."""
    existing = _SECTION.search(text)
    if existing:
        return text[:existing.start()] + section + "\n" + text[existing.end():].lstrip("\n")
    anchor = re.search(r"(?m)^##[ \t]+(?:Взгляд: |(?:Полная )?[Сс]тенограмма[ \t]*$)", text)
    if anchor:
        return text[:anchor.start()] + section + "\n" + text[anchor.start():]
    return text.rstrip() + "\n\n" + section


def _clean(text: object) -> str:
    return " ".join(str(text or "").split()).lstrip("-– ").strip()[:MAX_TEXT]


def _color(value: object) -> str:
    color = str(value or DEFAULT_COLOR)
    if color not in COLORS:
        raise BookmarkError("Такого цвета нет: " + ", ".join(COLORS))
    return color


def _mutate(core, meeting_id: str, change) -> tuple[dict | None, bool]:
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise BookmarkError("Запись не найдена", 404)
    with _lock:
        text = meeting.path.read_text(encoding="utf-8")
        parsed = parse_content(text)
        result, created = change(parsed, meeting)
        if parsed.items or parsed.extras:
            new_text = _place(text, render(parsed))
        else:   # закладок не осталось: раздела в заметке нет, как будто его и не добавляли
            existing = _SECTION.search(text)
            new_text = text[:existing.start()] + text[existing.end():].lstrip("\n") if existing else text
        if new_text != text:
            _write(meeting, new_text)
    if new_text != text:
        core.reload(force=True)
    return result, created


def add(core, meeting_id: str, sec: int, text: str = "", color: str = DEFAULT_COLOR) -> dict:
    """Ставит закладку. Повторная закладка на той же секунде новую не создаёт: комментарий и цвет обновляются, если даны."""
    def change(parsed: Parsed, meeting) -> tuple[dict, bool]:
        if sec < 0 or sec > 48 * 3600:
            raise BookmarkError("Время закладки вне записи")
        limit = knowledge.to_seconds(meeting.duration or "")
        if limit and sec > limit + 30:
            raise BookmarkError("Это время позже конца записи")
        existing = next((i for i in parsed.items if i["sec"] == sec), None)
        if existing:
            if _clean(text):
                existing["text"] = _clean(text)
            if color and color != DEFAULT_COLOR:
                existing["color"] = _color(color)
            return dict(existing), False
        if len(parsed.items) >= MAX_PER_RECORD:
            raise BookmarkError(f"Закладок у записи не больше {MAX_PER_RECORD}")
        item = {"sec": sec, "t": stamp(sec), "text": _clean(text), "color": _color(color)}
        parsed.items.append(item)
        parsed.items.sort(key=lambda i: i["sec"])
        return dict(item), True
    result, created = _mutate(core, meeting_id, change)
    return {**result, "created": created}


def update(core, meeting_id: str, sec: int, text: str | None = None, color: str | None = None) -> dict:
    def change(parsed: Parsed, _meeting) -> tuple[dict, bool]:
        item = next((i for i in parsed.items if i["sec"] == sec), None)
        if not item:
            raise BookmarkError("Такой закладки нет", 404)
        if text is not None:
            item["text"] = _clean(text)
        if color is not None:
            item["color"] = _color(color)
        return dict(item), False
    return _mutate(core, meeting_id, change)[0]


def remove(core, meeting_id: str, sec: int) -> bool:
    def change(parsed: Parsed, _meeting) -> tuple[dict | None, bool]:
        before = len(parsed.items)
        parsed.items = [i for i in parsed.items if i["sec"] != sec]
        return ({"ok": True} if len(parsed.items) != before else None), False
    return _mutate(core, meeting_id, change)[0] is not None
