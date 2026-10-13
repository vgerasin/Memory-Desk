"""Сохранение ответа чата в Obsidian: заметка `Ответы/<дата> — <вопрос> · <отпечаток>.md` с источниками и таймкодами.

Ссылки на записи — внутренние ссылки Obsidian, их путь и название берутся из архива по идентификатору записи, а не из
присланного интерфейсом текста. Повторное сохранение того же ответа ничего не пишет: заметку могли уже править в Obsidian.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from . import knowledge, materials

FOLDER = "Ответы"
MAX_SOURCES = 10


def _line(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _escape(answer: str) -> str:
    """Строки ответа, похожие на заголовки или служебные блоки, не должны ломать структуру заметки."""
    return "\n".join(("\\" + line if re.match(r"^\s*(#|>\s*\[!|---\s*$)", line) else line) for line in answer.strip().splitlines())


def digest_of(question: str, answer: str) -> str:
    return hashlib.md5(f"{question.strip()}\n{answer.strip()}".encode("utf-8")).hexdigest()[:6]


def target_path(question: str, answer: str, when: datetime) -> Path:
    folder = knowledge.WORKSPACE_DIR / FOLDER
    stem = f"{when:%Y-%m-%d} — {materials.safe_name(question, 60)} · {digest_of(question, answer)}"
    return folder / (stem + ".md")


def render(core, question: str, answer: str, sources: list[dict], scope: str, when: datetime) -> str:
    question, answer = _line(question, 500), answer.strip()
    lines = [
        "---", "type: chat_answer", f"date: {when:%Y-%m-%d}", f'saved_at: "{when:%Y-%m-%dT%H:%M:%S}"',
        f"question: {json.dumps(question, ensure_ascii=False)}", f"scope: {json.dumps(_line(scope, 120) or 'Весь архив', ensure_ascii=False)}",
        "---", "", f"# {question}", "",
        "> [!info] Ответ Memory Desk",
        f"> Сохранён {when:%d.%m.%Y %H:%M}. Область поиска: {_line(scope, 120) or 'Весь архив'}. "
        "Ответ составлен локальной моделью по вашим записям: проверяйте важное по таймкодам.",
        "", _escape(answer), "",
    ]
    items, seen = [], set()
    for source in sources[:MAX_SOURCES]:
        meeting = core.by_id.get(str(source.get("meetingId") or ""))
        stamp = _line(source.get("t"), 12)
        key = (meeting.id if meeting else _line(source.get("title"), 80), stamp)
        if key in seen:
            continue
        seen.add(key)
        title = _line(meeting.title if meeting else source.get("title") or "Запись", 120).replace("|", "/").replace("]", ")").replace("[", "(")
        link = f"[[{knowledge.note_relative_path(meeting.path).removesuffix('.md')}|{title}]]" if meeting else title
        day = re.sub(r"^(\d{4})-(\d{2})-(\d{2})$", r"\3.\2.\1", _line(source.get("date"), 20))
        when_line = ", ".join(x for x in (day, _line(source.get("time"), 8)) if x)
        head = f"- {link}" + (f" — {when_line}" if when_line else "") + (f" · `⏱ {stamp}`" if stamp else "")
        excerpt = _line(source.get("excerpt"), 400)
        items.append(head + (f"\n  > «{excerpt}»" if excerpt else ""))
    if items:
        lines += ["## Источники", "", *items, ""]
    return "\n".join(lines)


def save(core, question: str, answer: str, sources: list[dict], scope: str = "", when: datetime | None = None) -> dict:
    """Сохраняет ответ. Возвращает путь, ссылку Obsidian и признак `already` (такая заметка уже есть и не тронута)."""
    question, answer = question.strip(), answer.strip()
    if not question or not answer:
        raise ValueError("Нечего сохранять: нужны вопрос и ответ")
    when = when or datetime.now()
    path = target_path(question, answer, when)
    if not path.exists():
        # тот же ответ, сохранённый в другой день, — тот же файл: ищем по отпечатку
        same = sorted(path.parent.glob(f"* · {digest_of(question, answer)}.md")) if path.parent.exists() else []
        if same:
            path = same[0]
    already = path.exists()
    if not already:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name("." + path.name + ".tmp")
        temp.write_text(render(core, question, answer, sources, scope, when), encoding="utf-8")
        temp.replace(path)
    return {"savedAs": knowledge.note_relative_path(path), "obsidianUrl": knowledge.obsidian_url(path), "already": already}
