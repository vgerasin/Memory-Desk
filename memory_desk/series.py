"""Серии встреч: повторяющиеся встречи складываются в линию с ответом «что изменилось с прошлого раза»
и «какие вопросы висят уже третью встречу подряд».

Серия определяется без календаря: записи одного проекта с похожими названиями («Планёрка КХД», «Планёрка КХД 12.10»),
а если названия различаются, но состав участников совпадает, нужно более слабое сходство названий.
Это гипотеза: она только показывается, ничего не меняет в заметках."""
from __future__ import annotations

import hashlib
import re

from . import knowledge, storage

TITLE_SIMILARITY = 0.6          # сходство названий внутри одного проекта
TITLE_SIMILARITY_PEOPLE = 0.4   # то же, если совпадает непустой состав участников
NO_PROJECT_SIMILARITY = 0.75    # без проекта доверяем только очень похожим названиям
QUESTION_SIMILARITY = 0.5       # один и тот же вопрос в разных записях
REPEAT_FROM = 3                 # «висит» с такого числа встреч подряд

_WORD = re.compile(r"[a-zа-яё]+", re.IGNORECASE)


def tokens(text: str) -> frozenset[str]:
    """Основы слов для сравнения: числа и короткие слова отбрасываются, окончания срезаются грубо (по длине слова)."""
    out = set()
    for word in _WORD.findall((text or "").casefold().replace("ё", "е")):
        if len(word) < 3:
            continue
        out.add(word[:4] if len(word) >= 7 else word[:3])
    return frozenset(out)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _order_key(m: knowledge.Meeting) -> tuple:
    return (m.recorded_at or m.date or "", m.time or "")


def _project_key(m: knowledge.Meeting) -> str:
    return m.project_id or m.project if (m.project_id or m.project) not in {"", "Без проекта"} else ""


def build_series(meetings: list[knowledge.Meeting]) -> list[list[knowledge.Meeting]]:
    """Группы из двух и более записей, каждая от старой к новой."""
    items = sorted(meetings, key=_order_key)
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    words = [tokens(m.title) for m in items]
    people = [frozenset(" ".join(p.casefold().split()) for p in m.participants if p) for m in items]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _project_key(items[i]) != _project_key(items[j]):
                continue
            score = similarity(words[i], words[j])
            if _project_key(items[i]):
                limit = TITLE_SIMILARITY_PEOPLE if people[i] and people[i] == people[j] else TITLE_SIMILARITY
            else:
                limit = NO_PROJECT_SIMILARITY
            if score >= limit:
                parent[find(j)] = find(i)
    groups: dict[int, list[knowledge.Meeting]] = {}
    for index, meeting in enumerate(items):
        groups.setdefault(find(index), []).append(meeting)
    return [g for g in groups.values() if len(g) >= 2]


def series_id(group: list[knowledge.Meeting]) -> str:
    """Устойчивый номер серии: по самой ранней записи, поэтому новые встречи его не меняют."""
    return hashlib.md5(group[0].id.encode()).hexdigest()[:12]


def _brief(m: knowledge.Meeting) -> dict:
    return {"id": m.id, "title": m.title, "date": m.date, "time": m.time}


def _questions(m: knowledge.Meeting) -> list[dict]:
    return [{"text": f["text"], "tokens": tokens(f["text"]), "sec": f["sec"]} for f in knowledge.fact_items(m, "Открытые вопросы")]


def _same(a: dict, b: dict) -> bool:
    return similarity(a["tokens"], b["tokens"]) >= QUESTION_SIMILARITY


def _item(row: dict, **extra) -> dict:
    return {"id": row["id"], "text": row["text"], "owner": (row.get("owner") or "").strip(), "status": row["status"], **extra}


def describe(group: list[knowledge.Meeting], current_id: str) -> dict:
    """Серия глазами записи `current_id`: позиция, что изменилось с прошлой встречи, вопросы, висящие подряд."""
    ids = [m.id for m in group]
    index = ids.index(current_id)
    current = group[index]
    previous = group[index - 1] if index > 0 else None

    def rows(table: str, meeting_id: str) -> list[dict]:
        return storage.rows(f"SELECT * FROM {table} WHERE meeting_id=? AND status!='rejected' ORDER BY rowid", (meeting_id,))

    out: dict = {
        "id": series_id(group), "title": group[-1].title, "count": len(group), "position": index + 1,
        "meetings": [{**_brief(m), "current": m.id == current_id} for m in group],
        "previous": _brief(previous) if previous else None,
        "decisionsNew": [], "tasksNew": [], "tasksClosed": [], "tasksOpen": [],
        "questionsNew": [], "questionsResolved": [], "questionsRepeated": [],
    }
    now_tasks, now_decisions = rows("tasks", current.id), rows("decisions", current.id)
    out["decisionsNew"] = [_item(r) for r in now_decisions]
    out["tasksNew"] = [_item(r) for r in now_tasks]
    if previous:
        prev_tasks = rows("tasks", previous.id)
        out["tasksClosed"] = [_item(r) for r in prev_tasks if r["status"] == "done"]
        out["tasksOpen"] = [_item(r, deadline=(r.get("deadline") or "").strip()) for r in prev_tasks if r["status"] in {"open", "draft"}]
        prev_q, now_q = _questions(previous), _questions(current)
        out["questionsNew"] = [{"text": q["text"], "sec": q["sec"]} for q in now_q if not any(_same(q, p) for p in prev_q)]
        out["questionsResolved"] = [{"text": p["text"]} for p in prev_q if not any(_same(p, q) for q in now_q)]
    # вопрос, который встречается подряд во всех записях до текущей включительно (не меньше REPEAT_FROM)
    for question in _questions(current):
        streak, first = 1, current
        for earlier in reversed(group[:index]):
            if any(_same(question, q) for q in _questions(earlier)):
                streak += 1
                first = earlier
            else:
                break
        if streak >= REPEAT_FROM:
            out["questionsRepeated"].append({"text": question["text"], "sec": question["sec"], "count": streak, "since": first.date})
    return out


def for_meeting(live: list[knowledge.Meeting], meeting_id: str) -> dict | None:
    for group in build_series(live):
        if any(m.id == meeting_id for m in group):
            return describe(group, meeting_id)
    return None


def listing(live: list[knowledge.Meeting]) -> list[dict]:
    result = []
    for group in build_series(live):
        latest = group[-1]
        repeated = describe(group, latest.id)["questionsRepeated"]
        result.append({"id": series_id(group), "title": latest.title, "projectId": latest.project_id or None,
                       "project": latest.project if _project_key(latest) else None, "count": len(group),
                       "first": group[0].date, "last": latest.date, "latestId": latest.id, "repeatedQuestions": len(repeated)})
    return sorted(result, key=lambda s: s["last"], reverse=True)
