"""Подсказки «что спросить дальше» под ответом чата. Без модели и без задержки: по найденным источникам, открытым
вопросам записи и тому, что в записи есть (решения, задачи, риски). Заданные вопросы не повторяются; если подходящего
нет, остаются общие («Что решили?», «Кто за что отвечает?»)."""
from __future__ import annotations

import re

from . import knowledge, storage

LIMIT = 3
GENERIC = ("Что решили?", "Кто за что отвечает?", "Какие риски названы?", "Какие вопросы остались открытыми?",
           "Какие задачи ещё открыты?")


def _words(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[\w]+", text.casefold().replace("ё", "е")) if len(w) > 2}


def _same(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return a.strip().casefold() == b.strip().casefold()
    return len(wa & wb) / len(wa | wb) >= 0.7


def _short(title: str, limit: int = 60) -> str:
    title = " ".join(title.split())
    return title if len(title) <= limit else title[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _open_question(text: str) -> str:
    text = " ".join(text.split()).rstrip(" .;")
    if len(text) > 110:
        text = text[:110].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return text if text.endswith("?") else text + "?"


def suggest(core, question: str, sources: list[dict], asked: list[str] | None = None, scope_kind: str = "all") -> list[str]:
    """До трёх вопросов. `sources` — источники ответа (с `meetingId`), `asked` — уже заданные в беседе вопросы."""
    asked = [q for q in (asked or []) if q.strip()] + [question]
    found: list[str] = []

    def add(text: str) -> None:
        text = text.strip()
        if text and len(found) < LIMIT and not any(_same(text, q) for q in asked + found):
            found.append(text)

    meetings, seen = [], set()
    for source in sources:
        meeting = core.by_id.get(str(source.get("meetingId") or ""))
        if meeting and meeting.id not in seen:
            seen.add(meeting.id)
            meetings.append(meeting)
    for meeting in meetings[:2]:
        for fact in knowledge.fact_items(meeting, "Открытые вопросы")[:1]:
            add(_open_question(fact["text"]))
    for meeting in meetings[:1 if (len(meetings) == 1 or scope_kind == "record") else 2]:
        about = "" if scope_kind == "record" else f" «{_short(meeting.title)}»"
        has = {table: storage.rows(f"SELECT 1 FROM {table} WHERE meeting_id=? AND status {cond} LIMIT 1", (meeting.id,))
               for table, cond in (("decisions", "!='rejected'"), ("tasks", "IN ('draft','open')"))}
        if has["decisions"]:
            add(f"Какие решения приняты на{about}?" if about else "Какие решения приняты?")
        if has["tasks"]:
            add(f"Какие задачи по{about} ещё открыты?" if about else "Какие задачи ещё открыты?")
        if knowledge.fact_items(meeting, "Риски"):
            add(f"Какие риски названы на{about}?" if about else "Какие риски названы?")
    for text in GENERIC:
        add(text)
    return found
