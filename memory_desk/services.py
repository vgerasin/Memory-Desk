"""Сборка данных для интерфейса: DTO по сущностям из дизайн-референса (Meeting, Job, Task, ...)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from . import attachments, bookmarks, config, daily, dictionary, digest, followups, integrations, jobs, knowledge, materials, ocr, speakers as speakers_mod, storage, templates, voice
from .core import Core

PALETTE = ["#5E5CE6", "#FF9F0A", "#30B0C7", "#34C759", "#FF375F", "#AF52DE", "#64D2FF", "#A2845E", "#FF6482", "#BF5AF2"]
NO_PROJECT_COLOR = "#8E8E93"
NO_PROJECT = "Без проекта"
NO_TYPE = "Не указан"


def color_for(key: str) -> str:
    return PALETTE[int(hashlib.md5(key.encode()).hexdigest(), 16) % len(PALETTE)]


def initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    return "".join(p[0].upper() for p in parts[:2]) or "?"


def person_dto(person: dict) -> dict:
    try:
        aliases = json.loads(person.get("aliases") or "[]")
    except (TypeError, ValueError):
        aliases = []
    return {"id": person["id"], "name": person["name"], "role": person.get("role") or "",
            "organization": person.get("organization") or "", "aliases": aliases if isinstance(aliases, list) else [],
            "notes": person.get("notes") or "",
            "initials": initials(person["name"]), "color": color_for("person:" + person["name"].casefold())}


def clock_seconds(value: str) -> int | None:
    return knowledge.to_seconds(value)


def project_dto(m: knowledge.Meeting) -> dict | None:
    if not m.project_id and (not m.project or m.project == NO_PROJECT):
        return None
    key = m.project_id or m.project
    return {"id": m.project_id or None, "title": m.project, "color": color_for("project:" + key)}


def is_iso_date(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def meeting_date(m: knowledge.Meeting) -> str:
    if is_iso_date(m.date):
        return m.date
    return (m.recorded_at or "")[:10]


# --------------------------------------------------------------------------- встречи

def meeting_item(core: Core, m: knowledge.Meeting, rows: dict, people_map: dict) -> dict:
    row = rows.get(m.id, {})
    summary = knowledge.summary_bullets(m, 1)
    people = [person_dto(p) for p in people_map.get(m.id, [])]
    seconds = clock_seconds(m.duration)
    return {
        "id": m.id, "title": m.title, "date": meeting_date(m), "time": m.time,
        "duration": m.duration, "durationSec": seconds,
        "project": project_dto(m),
        "type": {"id": m.record_type if m.source_type != NO_TYPE else "", "title": m.source_type or NO_TYPE},
        "people": people,
        "status": "needs_review" if row.get("review_status", "needs_review") == "needs_review" else "completed",
        "excerpt": summary[0] if summary else "",
        "important": bool(row.get("important")),
        "archived": bool(row.get("archived")),
    }


def _task_dto(row: dict, meeting_title: str = "", meeting_date_value: str = "") -> dict:
    deadline = (row.get("deadline") or "").strip()
    due_iso = deadline if is_iso_date(deadline) else None
    due_text = "" if due_iso or deadline.casefold() in {"", "не указан", "не указано"} else deadline
    owner = (row.get("owner") or "").strip()
    if owner.casefold() in {"не указано", "не указан"}:
        owner = ""
    t = row.get("evidence_time") or ""
    return {
        "id": row["id"], "text": row["text"], "owner": owner, "due": due_iso, "dueText": due_text,
        "t": t, "tSec": clock_seconds(t), "status": row["status"],
        "draft": row["status"] == "draft", "done": row["status"] == "done",
        "meetingId": row["meeting_id"], "meetingTitle": meeting_title, "meetingDate": meeting_date_value,
        **daily.stuck_fields(row, meeting_date_value),
    }


def _decision_dto(row: dict, meeting_title: str = "", meeting_date_value: str = "") -> dict:
    t = row.get("evidence_time") or ""
    return {
        "id": row["id"], "text": row["text"], "owner": (row.get("owner") or "").strip(), "t": t,
        "tSec": clock_seconds(t), "status": row["status"], "draft": row["status"] == "draft",
        "meetingId": row["meeting_id"], "meetingTitle": meeting_title, "meetingDate": meeting_date_value,
        "replacedBy": row.get("replaced_by") or None,
    }


def list_meetings(core: Core, *, q: str = "", project: str = "", person: str = "", type_: str = "",
                  status: str = "", period: str = "", archived: bool = False, limit: int = 0, offset: int = 0) -> dict:
    rows = {r["id"]: r for r in storage.rows("SELECT * FROM meetings")}
    people_map = storage.people_by_meeting()
    base = [m for m in core.meetings if (rows.get(m.id, {}).get("archived", 0) == 1) == archived]
    base.sort(key=lambda m: (m.recorded_at or m.date, m.time), reverse=True)
    today = date.today()
    query = q.strip().casefold()
    matched = []
    for m in base:
        row = rows.get(m.id, {})
        review = row.get("review_status", "needs_review") == "needs_review"
        if project:
            current = (m.project or NO_PROJECT)
            if project == NO_PROJECT:
                if project_dto(m) is not None:
                    continue
            elif current != project and m.project_id != project:
                continue
        if type_ and (m.source_type or NO_TYPE) != type_:
            continue
        if person and person not in {p["name"] for p in people_map.get(m.id, [])}:
            continue
        if status == "needs_review" and not review:
            continue
        if status == "completed" and review:
            continue
        if period:
            d = meeting_date(m)
            if not is_iso_date(d):
                continue
            age = (today - date.fromisoformat(d)).days
            if (period == "today" and age != 0) or (period == "7d" and age > 7) or (period == "30d" and age > 30):
                continue
        if query:
            haystack = " ".join([m.title, " ".join(knowledge.summary_bullets(m, 20)),
                                 knowledge.transcript_text_cached(m)]).casefold()
            if query not in haystack:
                continue
        matched.append(m)
    # карточки собираем только для показанной страницы: на больших архивах это основная стоимость
    page = matched[offset:offset + limit] if limit else matched
    items = [meeting_item(core, m, rows, people_map) for m in page]
    total_live = [m for m in core.meetings if rows.get(m.id, {}).get("archived", 0) == 0]
    review_count = sum(1 for m in total_live if rows.get(m.id, {}).get("review_status", "needs_review") == "needs_review")
    return {"items": items, "total": len(matched), "counts": {"total": len(total_live), "needsReview": review_count,
                                       "noProject": sum(1 for m in total_live if project_dto(m) is None)}}


_KNOWN_SECTIONS = {t.casefold() for t in templates.STANDARD.values()} | {"краткое резюме", "стенограмма", "полная стенограмма", "закладки", "вложения"}


def extra_sections(m: knowledge.Meeting) -> list[dict]:
    """Свои разделы шаблона (всё, что кабинет не читает как стандартное), с пунктами и таймкодами."""
    result = []
    for title, body in knowledge.note_sections(m).items():
        if title.casefold() in _KNOWN_SECTIONS:
            continue
        items = [{"text": f["text"], "t": f["evidence_time"], "tSec": clock_seconds(f["evidence_time"])}
                 for f in (storage.parse_fact(line) for line in storage.bullet_lines(body)) if f["text"]]
        result.append({"title": title, "items": items})
    return result


def bookmark_dtos(m: knowledge.Meeting, transcript: list[dict]) -> list[dict]:
    """Закладки записи; к каждой — реплика стенограммы в этот момент (для списка), она не хранится."""
    result = []
    for mark in bookmarks.list_for(m):
        line = next((seg for seg in reversed(transcript) if float(seg["sec"]) <= mark["sec"]), None)
        context = " ".join(str(line["text"]).split()) if line else ""
        result.append({**mark, "context": context if len(context) <= 140 else context[:140].rsplit(" ", 1)[0] + "…"})
    return result


def meeting_detail(core: Core, m: knowledge.Meeting) -> dict:
    rows = {r["id"]: r for r in storage.rows("SELECT * FROM meetings WHERE id=?", (m.id,))}
    item = meeting_item(core, m, rows, storage.people_by_meeting())
    decisions = [_decision_dto(r, m.title, meeting_date(m)) for r in storage.rows(
        "SELECT * FROM decisions WHERE meeting_id=? AND status!='rejected' ORDER BY rowid", (m.id,))]
    tasks = [_task_dto(r, m.title, meeting_date(m)) for r in storage.rows(
        "SELECT * FROM tasks WHERE meeting_id=? AND status!='rejected' ORDER BY rowid", (m.id,))]

    def facts(*names):
        return [{"text": f["text"], "t": f["evidence_time"], "tSec": f["sec"]} for f in knowledge.fact_items(m, *names)]

    audio = knowledge.meeting_audio(m)
    transcript = knowledge.transcript_segments(m)
    template = templates.get(m.template or None)
    overlaps = voice.meeting_overlaps(m.id)
    if overlaps:
        transcript = [{**seg, "overlap": True} if float(seg["sec"]) in overlaps else seg for seg in transcript]
    weak = knowledge.transcript_weak(m)
    if weak:
        transcript = [{**seg, "weak": True} if int(seg["sec"]) in weak else seg for seg in transcript]
    known = {r["name"].casefold(): r["id"] for r in storage.rows("SELECT id,name FROM people")}
    prints = voice.meeting_voices(m.id)
    speaker_list = [{**sp, "personId": known.get(sp["label"].casefold()),
                     "voice": ({"matched": prints[sp["label"]]["matched"], "score": prints[sp["label"]]["score"]}
                               if sp["label"] in prints else None)}
                    for sp in speakers_mod.summarize(transcript)]
    return {
        **item,
        "summary": knowledge.summary_bullets(m, 12),
        "topics": [f["text"] for f in knowledge.fact_items(m, "Основные темы")],
        "decisions": decisions, "tasks": tasks,
        "risks": facts("Риски"), "questions": facts("Открытые вопросы"), "nextSteps": facts("Следующие шаги"),
        "template": {"id": template.id if m.template else "", "name": template.name if m.template else "",
                     "suggestedId": templates.suggest(m.record_type).id},
        "extraSections": extra_sections(m), "bookmarks": bookmark_dtos(m, transcript),
        "attachments": attachments.list_for(m),
        "transcript": transcript, "speakers": speaker_list, "diarization": speakers_mod.status(m.id),
        "obsidianUrl": knowledge.obsidian_url(m.path), "notePath": knowledge.note_relative_path(m.path),
        "hasAudio": audio is not None, "audioUrl": f"/api/meetings/{m.id}/audio" if audio else None,
    }


# --------------------------------------------------------------------------- проекты, люди, задачи

def projects_overview(core: Core) -> list[dict]:
    meetings = core.live_meetings()
    task_rows = storage.rows("SELECT t.*, m.project_title, m.project_id AS mp FROM tasks t JOIN meetings m ON m.id=t.meeting_id "
                             "WHERE t.status IN ('draft','open') AND m.archived=0")
    people_map = storage.people_by_meeting()
    result = []
    for project in core.projects():
        linked = [m for m in meetings if m.project_id == project["id"] or (not m.project_id and m.project == project["title"])]
        questions = sum(len(knowledge.fact_items(m, "Открытые вопросы")) for m in linked)
        open_tasks = sum(1 for t in task_rows if t["mp"] == project["id"] or t["project_title"] == project["title"])
        people: dict[str, dict] = {}
        for m in linked:
            for person in people_map.get(m.id, []):
                people[person["id"]] = person_dto(person)
        if linked:
            latest = linked[0]
            decision = storage.one("SELECT text FROM decisions WHERE meeting_id=? AND status!='rejected' ORDER BY rowid LIMIT 1",
                                   (latest.id,))
            change = {"text": decision["text"] if decision else (knowledge.summary_bullets(latest, 1) or [""])[0],
                      "kind": "decision" if decision else "summary", "meetingId": latest.id, "date": meeting_date(latest)}
        else:
            change = None
        stored = storage.one("SELECT goal,description FROM projects WHERE id=?", (project["id"],)) or {}
        result.append({
            "id": project["id"], "title": project["title"], "color": color_for("project:" + project["id"]),
            "status": project.get("status") or "active",
            "goal": project.get("goal") or stored.get("goal") or stored.get("description") or "",
            "counts": {"records": len(linked), "openTasks": open_tasks, "questions": questions},
            "people": list(people.values()), "lastChange": change,
        })
    return result


def person_names(person: dict) -> set[str]:
    """Как человека называют в задачах: полное имя, имя без фамилии и псевдонимы из карточки (всё в нижнем регистре)."""
    name = " ".join(person["name"].split()).casefold()
    names = {name}
    if name.split():
        names.add(name.split()[0])
    names.update(a.casefold() for a in person_dto(person)["aliases"])
    return names


def people_overview(core: Core) -> list[dict]:
    people = storage.rows("SELECT * FROM people ORDER BY name")
    links = storage.rows("SELECT mp.person_id, m.id, m.project_title, m.project_id, m.recorded_at FROM meeting_people mp "
                         "JOIN meetings m ON m.id=mp.meeting_id WHERE m.archived=0")
    tasks = storage.rows("SELECT t.*, m.title AS mt, m.meeting_date AS md FROM tasks t JOIN meetings m ON m.id=t.meeting_id "
                         "WHERE t.status IN ('draft','open') AND m.archived=0 ORDER BY t.updated_at DESC")
    result = []
    voices = voice.all_profile_stats()
    for person in people:
        mine = [l for l in links if l["person_id"] == person["id"]]
        names = person_names(person)
        owned = [t for t in tasks if (t.get("owner") or "").casefold() in names]
        projects = list(dict.fromkeys(l["project_title"] for l in mine if l["project_title"] and l["project_title"] != NO_PROJECT))
        last = _task_dto(owned[0], owned[0]["mt"], owned[0]["md"]) if owned else None
        result.append({**person_dto(person), "meetings": len(mine), "projects": projects,
                       "obligations": len(owned), "lastAgreement": last, "voice": voices.get(person["id"])})
    return result


def tasks_registry(core: Core, kind: str, limit: int = 0, offset: int = 0, stuck: bool = False) -> dict:
    meta = {r["id"]: r for r in storage.rows("SELECT id,title,meeting_date FROM meetings WHERE archived=0")}
    if kind == "decisions":
        raw = storage.rows("SELECT d.* FROM decisions d JOIN meetings m ON m.id=d.meeting_id WHERE d.status!='rejected' AND m.archived=0 "
                           "ORDER BY d.status='draft' DESC, m.recorded_at DESC")
        raw = [r for r in raw if r["meeting_id"] in meta]
        total = len(raw)
        raw = raw[offset:offset + limit] if limit else raw
        items = [_decision_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"]) for r in raw]
    else:
        raw = storage.rows("SELECT t.* FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status!='rejected' AND m.archived=0 "
                           "ORDER BY t.status='draft' DESC, t.status='done' ASC, COALESCE(NULLIF(t.deadline,''),'9') ASC, m.recorded_at DESC")
        raw = [r for r in raw if r["meeting_id"] in meta]
        if stuck:  # зависшие: самые старые первыми
            raw = [r for r in raw if daily.stuck_fields(r, meta[r["meeting_id"]]["meeting_date"])["stuck"]]
            raw.sort(key=lambda r: -daily.stuck_fields(r, meta[r["meeting_id"]]["meeting_date"])["ageDays"])
        total = len(raw)
        raw = raw[offset:offset + limit] if limit else raw
        items = [_task_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"]) for r in raw]
    counts = {
        "tasks": storage.one("SELECT COUNT(*) n FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status!='rejected' AND m.archived=0")["n"],
        "decisions": storage.one("SELECT COUNT(*) n FROM decisions d JOIN meetings m ON m.id=d.meeting_id WHERE d.status!='rejected' AND m.archived=0")["n"],
        "stuck": sum(1 for r in storage.rows("SELECT t.* FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status='open' AND m.archived=0")
                     if r["meeting_id"] in meta and daily.stuck_fields(r, meta[r["meeting_id"]]["meeting_date"])["stuck"]),
    }
    return {"items": items, "total": total, "counts": counts}


# --------------------------------------------------------------------------- «Сегодня»

def _hero_columns(core: Core, project_id: str | None, project_title: str | None, fallback: knowledge.Meeting | None) -> dict:
    meta = {r["id"]: r for r in storage.rows("SELECT id,title,meeting_date,project_id,project_title FROM meetings WHERE archived=0")}

    def in_scope(meeting_id: str) -> bool:
        row = meta.get(meeting_id)
        if not row:
            return False
        if project_id or project_title:
            return row["project_id"] == project_id or row["project_title"] == project_title
        return fallback is not None and meeting_id == fallback.id

    decisions = [_decision_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"]) for r in storage.rows(
        "SELECT d.* FROM decisions d JOIN meetings m ON m.id=d.meeting_id WHERE d.status!='rejected' AND m.archived=0 "
        "AND d.replaced_by IS NULL ORDER BY m.recorded_at DESC, d.rowid") if in_scope(r["meeting_id"])][:3]
    tasks = [_task_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"]) for r in storage.rows(
        "SELECT t.* FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status IN ('draft','open') AND m.archived=0 "
        "ORDER BY m.recorded_at DESC, t.rowid") if in_scope(r["meeting_id"])][:3]
    questions = []
    for m in core.live_meetings():
        if in_scope(m.id):
            for f in knowledge.fact_items(m, "Открытые вопросы"):
                questions.append({"text": f["text"], "tSec": f["sec"], "t": f["evidence_time"], "meetingId": m.id,
                                  "meetingTitle": m.title, "meetingDate": meeting_date(m)})
    return {"decisions": decisions, "tasks": tasks, "questions": questions[:3]}


def _thesis(text: str, limit: int = 150) -> str:
    """Короткая формулировка для списка: одна строка, обрезка по слову."""
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rsplit(" ", 1)[0].rstrip(",;:—-")
    return cut + "…"


def prep_brief(core: Core, project_id: str | None = None, meeting_id: str | None = None) -> dict:
    """Тезисы для подготовки к встрече: что менялось, решения, открытые задачи, вопросы, риски; у каждого — ссылка на источник."""
    live = core.live_meetings()
    if project_id:
        project = next((p for p in core.projects() if p["id"] == project_id), None)
        if not project:
            raise KeyError("Проект не найден")
        scope = [m for m in live if m.project_id == project["id"] or (not m.project_id and m.project == project["title"])]
        title = project["title"]
    elif meeting_id:
        one = core.by_id.get(meeting_id)
        if not one:
            raise KeyError("Запись не найдена")
        scope, title = [one], one.title
    else:
        scope, title = live[:5], "Весь архив"
    ids = {m.id for m in scope}
    by_id = {m.id: m for m in scope}
    meta = {r["id"]: r for r in storage.rows("SELECT id,title,meeting_date FROM meetings WHERE archived=0")}

    def link(meeting: knowledge.Meeting, sec: int | None = None, view: str | None = None) -> dict:
        return {"meetingId": meeting.id, "meetingTitle": meeting.title, "date": meeting_date(meeting), "tSec": sec, "view": view}

    changes = []
    for m in scope[:3]:
        bullet = (knowledge.summary_bullets(m, 1) or [""])[0]
        if bullet:
            changes.append({"text": _thesis(bullet), **link(m)})

    decisions = [{"text": _thesis(r["text"]), "draft": r["status"] == "draft", **link(by_id[r["meeting_id"]], clock_seconds(r["evidence_time"] or ""))}
                 for r in storage.rows("SELECT d.* FROM decisions d JOIN meetings m ON m.id=d.meeting_id WHERE d.status!='rejected' "
                                       "AND m.archived=0 AND d.replaced_by IS NULL ORDER BY m.recorded_at DESC, d.rowid") if r["meeting_id"] in ids][:6]

    today_iso = date.today().isoformat()
    raw_tasks = [r for r in storage.rows("SELECT t.* FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status IN ('draft','open') "
                                         "AND m.archived=0 ORDER BY m.recorded_at DESC, t.rowid") if r["meeting_id"] in ids]
    tasks = []
    for r in raw_tasks:
        dto = _task_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"])
        overdue = bool(dto["due"]) and dto["due"] < today_iso
        tasks.append({"text": _thesis(dto["text"]), "owner": dto["owner"], "due": dto["due"], "dueText": dto["dueText"],
                      "draft": dto["draft"], "overdue": overdue, **link(by_id[r["meeting_id"]], dto["tSec"])})
    tasks.sort(key=lambda t: (not t["overdue"], t["due"] or "9999"))
    tasks = tasks[:8]

    questions, risks = [], []
    for m in scope:
        for f in knowledge.fact_items(m, "Открытые вопросы"):
            questions.append({"text": _thesis(f["text"]), **link(m, f["sec"])})
        for f in knowledge.fact_items(m, "Риски"):
            risks.append({"text": _thesis(f["text"]), **link(m, f["sec"])})
    marked = []
    for m in scope:
        for mark in bookmarks.list_for(m):
            marked.append({"text": _thesis(mark["text"] or "Отмеченное место записи"), "color": mark["color"], **link(m, mark["sec"])})
    sections = [
        {"key": "changes", "title": "Что менялось", "empty": "Записей пока нет", "items": changes},
        {"key": "decisions", "title": "Решения", "empty": "Решений пока нет", "items": decisions},
        {"key": "tasks", "title": "Открытые задачи", "empty": "Открытых задач нет", "items": tasks},
        {"key": "questions", "title": "Открытые вопросы", "empty": "Открытых вопросов нет", "items": questions[:6]},
        {"key": "risks", "title": "Риски", "empty": "Рисков не отмечено", "items": risks[:4]},
    ]
    if marked:   # блок появляется, только если закладки есть
        sections.append({"key": "bookmarks", "title": "Отмечено вами на записях", "empty": "Закладок нет", "items": marked[:6]})
    brief = {"title": title, "projectId": project_id, "meetingId": meeting_id, "records": len(scope), "sections": sections}
    brief["headline"] = {"text": _structured_headline(brief), "mode": "structured"}
    return brief


def _structured_headline(brief: dict) -> str:
    """«Главное к встрече» без модели: просрочка, открытые задачи и вопросы, последнее решение."""
    if not brief["records"]:
        return "Записей пока нет — подготовиться не к чему."
    by_key = {s["key"]: s["items"] for s in brief["sections"]}
    tasks = by_key.get("tasks", [])
    overdue = sum(1 for t in tasks if t.get("overdue"))
    parts = []
    if tasks:
        parts.append((f"Просрочено {overdue} из {len(tasks)} открытых задач" if overdue else f"Открытых задач: {len(tasks)}"))
    if by_key.get("questions"):
        parts.append(f"без ответа вопросов: {len(by_key['questions'])}")
    if by_key.get("risks"):
        parts.append(f"отмеченных рисков: {len(by_key['risks'])}")
    line = ", ".join(parts)
    line = (line[0].upper() + line[1:] + ".") if line else "Открытых задач, вопросов и рисков нет."
    if by_key.get("decisions"):
        line += f" Последнее решение: {by_key['decisions'][0]['text']}"
    return line


_headline_cache: dict[tuple, dict] = {}


def prep_headline(core: Core, project_id: str | None = None, meeting_id: str | None = None) -> dict:
    """Одно-два предложения «Главное к встрече»: пишет локальная модель по тезисам подготовки; без модели — строка из цифр.
    Результат кэшируется до следующего изменения данных."""
    key = (project_id or "", meeting_id or "", core.data_version)
    if key in _headline_cache:
        return _headline_cache[key]
    brief = prep_brief(core, project_id, meeting_id)
    result = dict(brief["headline"])
    if brief["records"]:
        lines = []
        for section in brief["sections"]:
            for item in section["items"][:5]:
                extra = ""
                if section["key"] == "tasks":
                    extra = f" ({item.get('owner') or 'без ответственного'}; срок: {item.get('due') or item.get('dueText') or 'не указан'}{'; ПРОСРОЧЕНО' if item.get('overdue') else ''})"
                lines.append(f"[{section['title']}] {item['text']}{extra}")
        prompt = ("Ты помощник по архиву рабочих встреч. Перед встречей человеку нужны две фразы «Главное к встрече»: что важнее всего "
                  "помнить — просроченное, нерешённые вопросы, недавние решения. Пиши только по фактам ниже, ничего не выдумывай, "
                  "не используй списки, заголовки и вступления, не больше двух предложений, по-русски.\n\nФакты:\n"
                  + "\n".join(lines) + "\n\nГЛАВНОЕ:\n")
        try:
            text = digest.generate(prompt, 180).strip()
        except OSError:
            text = ""
        text = " ".join(re.sub(r"[*#>`]", "", text).split())
        if len(text) >= 20:
            result = {"text": text[:420].rsplit(" ", 1)[0] + "…" if len(text) > 420 else text, "mode": "model"}
    if result["mode"] == "model":  # без модели не кэшируем: после запуска Ollama следующее открытие уже получит текст модели
        if len(_headline_cache) > 50:
            _headline_cache.clear()
        _headline_cache[key] = result
    return result


# --------------------------------------------------------------------------- аналитика

def decision_tree(core: Core, project: str = "", days: int = 0) -> dict:
    """Дерево решений: по проектам; корень — действующее решение, ветви — то, что оно заменило (по цепочке «заменено»)."""
    today = date.today()
    cut = (today - timedelta(days=days)).isoformat() if days else ""
    by_id = {m.id: m for m in core.live_meetings()}
    rows = [r for r in storage.rows(
        "SELECT d.*, m.project_id mp, m.project_title mpt FROM decisions d JOIN meetings m ON m.id=d.meeting_id "
        "WHERE d.status!='rejected' AND m.archived=0 ORDER BY m.recorded_at, d.rowid") if r["meeting_id"] in by_id]
    nodes: dict[str, dict] = {}
    known = {r["id"] for r in rows}
    for r in rows:
        m = by_id[r["meeting_id"]]
        nodes[r["id"]] = {"id": r["id"], "text": r["text"], "owner": (r["owner"] or "").strip(), "status": r["status"], "draft": r["status"] == "draft",
                          "date": meeting_date(m), "meetingId": m.id, "meetingTitle": m.title, "tSec": clock_seconds(r["evidence_time"] or ""),
                          "projectId": r["mp"] or "", "project": r["mpt"] if r["mpt"] and r["mpt"] != NO_PROJECT else "",
                          "replacedBy": r["replaced_by"] if r["replaced_by"] in known else None, "replaces": []}
    for node in nodes.values():
        target = nodes.get(node["replacedBy"] or "")
        if target is not None:
            target["replaces"].append(node)
    groups: dict[str, dict] = {}
    for node in nodes.values():
        if cut and node["date"] < cut:
            continue
        if project and node["projectId"] != project:
            continue
        g = groups.setdefault(node["projectId"], {"id": node["projectId"], "title": node["project"] or "Без проекта", "current": [], "total": 0, "replaced": 0})
        g["total"] += 1
        if node["replacedBy"]:
            g["replaced"] += 1
        else:
            g["current"].append(node)
    for g in groups.values():
        g["current"].sort(key=lambda n: n["date"], reverse=True)
        g["active"] = g["total"] - g["replaced"]
    out = sorted(groups.values(), key=lambda g: (g["id"] == "", -g["total"], g["title"]))
    for g in out:
        g["color"] = color_for("project:" + g["title"]) if g["id"] else "#8E8E93"
    return {"today": today.isoformat(), "days": days, "projects": out}


def analytics(core: Core, days: int = 90) -> dict:
    """Данные для экрана «Аналитика»: активность по неделям, задачи, сроки, решения, связи людей и проектов."""
    today = date.today()
    live = core.live_meetings()
    cut = (today - timedelta(days=days)).isoformat() if days else ""
    in_range = [m for m in live if not cut or meeting_date(m) >= cut]
    ids = {m.id for m in in_range}
    by_id = {m.id: m for m in live}

    monday = today - timedelta(days=today.weekday())
    weeks = []
    for back in range(11, -1, -1):
        start = monday - timedelta(weeks=back)
        stop = start + timedelta(days=7)
        chosen = [m for m in live if is_iso_date(meeting_date(m)) and start.isoformat() <= meeting_date(m) < stop.isoformat()]
        weeks.append({"start": start.isoformat(), "meetings": len(chosen),
                      "minutes": round(sum(clock_seconds(m.duration) or 0 for m in chosen) / 60)})

    meta = {r["id"]: r for r in storage.rows("SELECT id,title,meeting_date FROM meetings WHERE archived=0")}
    task_rows = [r for r in storage.rows(
        "SELECT t.* FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status!='rejected' AND m.archived=0 "
        "ORDER BY m.recorded_at DESC, t.rowid") if r["meeting_id"] in ids and r["meeting_id"] in meta]
    tasks = [_task_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"]) for r in task_rows]
    today_iso = today.isoformat()
    for t in tasks:
        t["overdue"] = t["status"] in ("open", "draft") and bool(t["due"]) and t["due"] < today_iso

    owners: dict[str, dict] = {}
    for t in tasks:
        name = t["owner"] or "Не назначен"
        row = owners.setdefault(name, {"owner": name, "open": 0, "overdue": 0, "done": 0})
        if t["done"]:
            row["done"] += 1
        elif t["overdue"]:
            row["overdue"] += 1
        else:
            row["open"] += 1
    by_owner = sorted(owners.values(), key=lambda r: -(r["open"] + r["overdue"] + r["done"]))[:8]

    gantt = []
    for t in tasks:
        if t["status"] in ("open", "draft") and t["due"] and is_iso_date(t["meetingDate"]):
            m = by_id.get(t["meetingId"])
            gantt.append({"id": t["id"], "text": _thesis(t["text"], 90), "owner": t["owner"], "draft": t["draft"],
                          "project": (m.project if m and m.project != NO_PROJECT else ""),
                          "start": min(t["meetingDate"], t["due"]), "due": t["due"], "overdue": t["overdue"],
                          "meetingId": t["meetingId"], "tSec": t["tSec"]})
    gantt.sort(key=lambda g: (g["due"], g["start"]))

    decision_rows = [r for r in storage.rows(
        "SELECT d.* FROM decisions d JOIN meetings m ON m.id=d.meeting_id WHERE d.status!='rejected' AND m.archived=0 "
        "ORDER BY m.recorded_at DESC, d.rowid") if r["meeting_id"] in ids and r["meeting_id"] in meta]
    decisions = []
    for r in decision_rows[:80]:
        m = by_id[r["meeting_id"]]
        decisions.append({"id": r["id"], "text": _thesis(r["text"], 140), "draft": r["status"] == "draft",
                          "project": m.project if m.project != NO_PROJECT else "", "date": meeting_date(m),
                          "meetingId": m.id, "tSec": clock_seconds(r["evidence_time"] or "")})

    people_map = storage.people_by_meeting()
    links: dict[tuple[str, str], int] = {}
    person_total: dict[str, int] = {}
    project_total: dict[str, int] = {}
    for m in in_range:
        project = m.project if m.project and m.project != NO_PROJECT else ""
        for person in people_map.get(m.id, []):
            person_total[person["name"]] = person_total.get(person["name"], 0) + 1
            if project:
                links[(person["name"], project)] = links.get((person["name"], project), 0) + 1
        if project:
            project_total[project] = project_total.get(project, 0) + 1
    top_people = sorted(person_total, key=lambda n: -person_total[n])[:10]
    top_projects = sorted(project_total, key=lambda n: -project_total[n])[:8]
    graph = {
        "people": [{"name": n, "meetings": person_total[n], "initials": initials(n), "color": color_for("person:" + n.casefold())} for n in top_people],
        "projects": [{"name": n, "meetings": project_total[n], "color": color_for("project:" + n)} for n in top_projects],
        "links": [{"person": a, "project": b, "meetings": c} for (a, b), c in links.items() if a in top_people and b in top_projects],
    }

    minutes = sum(clock_seconds(m.duration) or 0 for m in in_range) // 60
    totals = {
        "records": len(in_range), "minutes": minutes,
        "openTasks": sum(1 for t in tasks if t["status"] in ("open", "draft") and not t["overdue"]),
        "overdue": sum(1 for t in tasks if t["overdue"]),
        "doneTasks": sum(1 for t in tasks if t["done"]),
        "decisions": len(decision_rows),
    }
    return {"days": days, "today": today_iso, "totals": totals, "weeks": weeks, "byOwner": by_owner,
            "gantt": gantt[:14], "ganttTotal": len(gantt), "decisions": decisions, "graph": graph}


def today_payload(core: Core) -> dict:
    now = datetime.now()
    live = core.live_meetings()
    rows = {r["id"]: r for r in storage.rows("SELECT * FROM meetings")}
    people_map = storage.people_by_meeting()

    hero = None
    event = integrations.next_meeting(core.cfg, now)
    if event:
        title_cf = event["title"].casefold()
        project = next((p for p in core.projects() if p["title"].casefold() in title_cf), None)
        hero = {
            "kind": "next_meeting", "title": event["title"], "start": event["start"], "end": event["end"],
            "ongoing": event["ongoing"], "people": [{"name": n, "initials": initials(n),
                                                      "color": color_for("person:" + n.casefold())} for n in event["people"]],
            "project": {"id": project["id"], "title": project["title"]} if project else None,
            "columns": _hero_columns(core, project["id"] if project else None, project["title"] if project else None,
                                     live[0] if live else None),
        }
    elif live:
        latest = live[0]
        item = meeting_item(core, latest, rows, people_map)
        hero = {
            "kind": "last_record", "title": latest.title, "meetingId": latest.id, "date": meeting_date(latest),
            "time": latest.time, "people": item["people"], "project": item["project"],
            "columns": _hero_columns(core, None, None, latest),
        }

    today = date.today()
    recent = [m for m in live if is_iso_date(meeting_date(m)) and (today - date.fromisoformat(meeting_date(m))).days <= 1]
    new_records = [meeting_item(core, m, rows, people_map) for m in (recent or live[:3])[:4]]

    meta = {r["id"]: r for r in storage.rows("SELECT id,title,meeting_date FROM meetings WHERE archived=0")}
    open_rows = storage.rows("SELECT t.* FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status='open' AND m.archived=0 "
                             "ORDER BY COALESCE(NULLIF(t.deadline,''),'9') ASC, t.updated_at DESC LIMIT 4")
    open_tasks = [_task_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"]) for r in open_rows if r["meeting_id"] in meta]
    drafts = storage.one("SELECT (SELECT COUNT(*) FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status='draft' AND m.archived=0) +"
                         "(SELECT COUNT(*) FROM decisions d JOIN meetings m ON m.id=d.meeting_id WHERE d.status='draft' AND m.archived=0) AS n")["n"]
    week_cut = (today - timedelta(days=7)).isoformat()
    week_rows = storage.rows("SELECT d.*, m.title mt, m.meeting_date md FROM decisions d JOIN meetings m ON m.id=d.meeting_id "
                             "WHERE d.status!='rejected' AND m.archived=0 AND m.meeting_date>=? ORDER BY m.recorded_at DESC, d.rowid LIMIT 4",
                             (week_cut,))
    return {
        "hero": hero, "newRecords": new_records, "tasks": open_tasks, "drafts": drafts,
        "decisions": [_decision_dto(r, r["mt"], r["md"]) for r in week_rows],
        "noProject": sum(1 for m in live if project_dto(m) is None),
        "reviewCount": sum(1 for m in live if rows.get(m.id, {}).get("review_status", "needs_review") == "needs_review"),
    }


# --------------------------------------------------------------------------- чат

def scope_paths(core: Core, scope: dict) -> set[str] | None:
    kind = (scope or {}).get("kind", "all")
    live = core.live_meetings()
    if kind == "record":
        m = core.by_id.get(scope.get("id", ""))
        return {str(m.path)} if m else set()
    if kind == "project":
        key = scope.get("id") or scope.get("title") or ""
        return {str(m.path) for m in live if m.project_id == key or m.project == scope.get("title") or m.project == key}
    if kind == "person":
        person = scope.get("id") or ""
        ids = {r["meeting_id"] for r in storage.rows(
            "SELECT mp.meeting_id FROM meeting_people mp JOIN people p ON p.id=mp.person_id WHERE p.id=? OR p.name=?", (person, person))}
        return {str(m.path) for m in live if m.id in ids}
    if kind == "days":
        try:
            days = min(max(int(scope.get("days") or 7), 1), 3650)
        except (TypeError, ValueError):
            raise ValueError("Период должен быть числом дней") from None
        cut = date.today() - timedelta(days=days)
        return {str(m.path) for m in live if is_iso_date(meeting_date(m)) and date.fromisoformat(meeting_date(m)) >= cut}
    return {str(m.path) for m in live}


def _excerpt(source: dict) -> str:
    raw = source.get("text", "")
    lines = []
    for line in raw.splitlines():
        line = re.sub(r"^\s*(?:[-*•]\s+|\d+\.\s+|- \[[ xX]\]\s*)", "", line)
        line = re.sub(r"^\[\d{1,2}:\d{2}:\d{2}\]\s*", "", line.strip())
        line = re.sub(r"[`*_]|⏱\s*\d{2}:\d{2}:\d{2}", "", line)
        line = re.sub(r"\s*[—–-]\s*$", "", line).strip()
        if line:
            lines.append(line)
    text = re.sub(r"\s+", " ", "; ".join(lines)).strip()
    return text[:220].rsplit(" ", 1)[0] + "…" if len(text) > 220 else text


def search_passages(core: Core, query: str, scope: dict | None = None, limit: int = 8) -> dict:
    """Поиск по записям без языковой модели: фрагменты с источниками (запись, время, раздел)."""
    query = " ".join((query or "").split())
    if not query:
        raise ValueError("Запрос не может быть пустым")
    limit = max(1, min(int(limit), 20))
    allowed = scope_paths(core, scope or {"kind": "all"})
    path_to_id = {str(m.path): m.id for m in core.meetings}
    found = core.index.search(query, paths=allowed, limit=limit * 2)
    items, seen = [], set()
    for hit in found:
        key = (str(hit["path"]), hit.get("timestamp"), hit.get("section"))
        if key in seen:
            continue
        seen.add(key)
        text = re.sub(r"\s+", " ", hit.get("text", "")).strip()
        items.append({"meetingId": path_to_id.get(str(hit["path"]), ""), "title": hit.get("title", ""), "date": hit.get("date", ""),
                      "time": hit.get("time", ""), "section": hit.get("section", ""), "t": hit.get("timestamp", ""),
                      "tSec": clock_seconds(hit.get("timestamp", "")), "text": text[:700],
                      "score": round(float(hit.get("rank_score", hit.get("score", 0.0))), 3)})
        if len(items) >= limit:
            break
    return {"query": query, "items": items, "total": len(items)}


def chat(core: Core, question: str, scope: dict, previous: list[str], asked: list[str] | None = None) -> dict:
    question = question.strip()
    if not question:
        raise ValueError("Вопрос не может быть пустым")
    made = materials.from_chat(core, question, scope, meeting_date)
    if made is not None:
        return made
    summary = digest.build(core, question, scope, meeting_date)
    if summary is not None:
        summary["suggestions"] = followups.suggest(core, question, summary.get("sources", []), asked, str(scope.get("kind") or "all"))
        return summary
    allowed = scope_paths(core, scope)
    path_to_id = {str(m.path): m.id for m in core.meetings}
    previous_paths = [str(core.by_id[i].path) for i in previous if i in core.by_id]
    sources, mode = knowledge.retrieve(core.index, core.meetings, question, previous_paths, allowed)
    answer = knowledge.answer_question(question, sources, mode)
    grouped: list[dict] = []
    seen = set()
    for source in sources:
        key = (source["path"], source.get("timestamp"))
        if key in seen:
            continue
        seen.add(key)
        meeting_id = path_to_id.get(str(source["path"]), "")
        grouped.append({
            "meetingId": meeting_id, "title": source.get("title", ""), "date": source.get("date", ""),
            "time": source.get("time", ""), "section": source.get("section", ""),
            "t": source.get("timestamp", ""), "tSec": clock_seconds(source.get("timestamp", "")),
            "excerpt": _excerpt(source),
        })
    shown = grouped[:4]
    return {"answer": answer, "sources": shown, "mode": mode, "confirmed": bool(sources),
            "suggestions": followups.suggest(core, question, shown, asked, str(scope.get("kind") or "all"))}


# --------------------------------------------------------------------------- система

def _ollama_status() -> tuple[str, str]:
    from . import llm
    chosen = config.llm_model()
    installed = llm.list_models(config.OLLAMA_URL, timeout=0.6)
    if installed is None:
        return "не отвечает", "warn"
    if llm.has_model(installed, chosen):
        return chosen, "ok"
    return "модель не загружена", "warn"


def system_status(core: Core) -> dict:
    cfg = core.cfg
    recordings, vault = Path(cfg["recordings_dir"]).expanduser(), Path(cfg["obsidian_dir"]).expanduser()
    import importlib.util
    whisper_ok = importlib.util.find_spec("mlx_whisper") is not None or (config.PROJECT_ROOT / ".venv/bin/mlx_whisper").exists()
    ollama_value, ollama_state = _ollama_status()
    try:
        free_gb = shutil.disk_usage(recordings if recordings.exists() else config.PROJECT_ROOT).free / 1024 ** 3
    except OSError:
        free_gb = 0
    backups = storage.list_backups()
    db_ok, db_message = storage.check_integrity()
    ocr_engines = ocr.engines()
    index = core.index
    if index.state == "ready":
        index_value, index_state = ("Qdrant · " + str(len(core.meetings)) + " записей"
                                    + (" (в памяти)" if index.mode == "memory_fallback" else "")), (
            "warn" if index.mode == "memory_fallback" else "ok")
    elif index.state == "error":
        index_value, index_state = "ошибка индекса", "bad"
    else:
        index_value, index_state = "готовится…", "warn"
    rows = [
        {"key": "whisper", "name": "Whisper", "value": "large-v3-turbo" if whisper_ok else "не найден", "state": "ok" if whisper_ok else "bad"},
        {"key": "ollama", "name": "Ollama", "value": ollama_value, "state": ollama_state},
        {"key": "index", "name": "Поисковый индекс", "value": index_value, "state": index_state},
        {"key": "recordings", "name": "Папка записей", "value": "доступ есть" if recordings.exists() else "не найдена",
         "state": "ok" if recordings.exists() else "bad"},
        {"key": "vault", "name": "Obsidian Vault", "value": "доступ есть" if vault.exists() else "не найден",
         "state": "ok" if vault.exists() else "bad"},
        {"key": "disk", "name": "Свободное место", "value": f"{free_gb:.0f} ГБ", "state": "ok" if free_gb > 20 else "warn"},
        {"key": "ocr", "name": "Распознавание текста на фото", "value": ", ".join(ocr_engines) if ocr_engines else "нет движка: фото сохраняются без текста",
         "state": "ok" if ocr_engines else "warn"},
        {"key": "database", "name": "База кабинета", "value": "цела" if db_ok else "повреждена: восстановите из копии",
         "state": "ok" if db_ok else "bad", "detail": db_message},
        {"key": "backup", "name": "Резервная копия",
         "value": datetime.fromtimestamp(backups[0].stat().st_mtime).strftime("%d.%m, %H:%M") if backups else "нет копии",
         "state": "ok" if backups else "warn"},
    ]
    order = {"ok": 0, "warn": 1, "bad": 2}
    overall = max((r["state"] for r in rows), key=lambda s: order[s])
    return {"rows": rows, "overall": overall, "indexState": index.state, "indexError": index.error}


def apply_dictionary(core: Core) -> dict:
    """Применяет словарь терминов к уже готовым стенограммам. Таймкоды и метки говорящих не меняются."""
    entries = dictionary.load()
    if not entries:
        return {"meetings": 0, "replacements": 0, "message": "Словарь пуст: добавьте хотя бы одно слово"}
    changed = replaced = 0
    for meeting in list(core.meetings):
        try:
            path, heading = knowledge._transcript_target(meeting)
            text = path.read_text(encoding="utf-8")
        except (ValueError, OSError):
            continue
        head, sep, body = text.partition(heading)
        if not sep:
            continue
        fixed, count = dictionary.apply_transcript(body, entries)
        if not count:
            continue
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(head + sep + fixed, encoding="utf-8")
        temp.replace(path)
        changed += 1
        replaced += count
    if changed:
        core.reload(force=True)
    message = (f"Исправлено слов: {replaced} в {changed} записях" if changed else "Исправлять нечего: все слова уже написаны верно")
    return {"meetings": changed, "replacements": replaced, "message": message}


# --------------------------------------------------------------------------- предложения в словарь из правок стенограммы

_EDIT_CACHE: dict[str, tuple[tuple, list[tuple[str, str]]]] = {}


def _original_transcript(meeting: knowledge.Meeting) -> Path | None:
    """Текст стенограммы в том виде, как его выдал распознавание (рабочая папка задания). Нет папки — сравнивать не с чем."""
    from . import asr
    if not meeting.source_audio:
        return None
    original = jobs._paths()["work"] / asr.slug(Path(meeting.source_audio).stem) / "transcript.md"
    return original if original.is_file() else None


def meeting_edits(meeting: knowledge.Meeting) -> list[tuple[str, str]]:
    """Что поправили руками в стенограмме записи: пары (было, стало)."""
    from . import asr, diarize
    original = _original_transcript(meeting)
    current = knowledge.transcript_file(meeting)
    if original is None or current is None:
        return []
    try:
        stamp = (original.stat().st_mtime_ns, current.stat().st_mtime_ns)
    except OSError:
        return []
    cached = _EDIT_CACHE.get(meeting.id)
    if cached and cached[0] == stamp:
        return cached[1]
    before: dict[int, list[str]] = {}
    for seg in diarize.parse(original.read_text(encoding="utf-8", errors="replace")):
        before.setdefault(seg["sec"], []).append(seg["text"])
    pairs: list[tuple[str, str]] = []
    seen: dict[int, int] = {}
    for seg in diarize.parse(knowledge.meeting_transcript(meeting)):
        slot = seen.get(seg["sec"], 0)
        seen[seg["sec"]] = slot + 1
        texts = before.get(seg["sec"], [])
        if slot < len(texts) and texts[slot] != seg["text"]:
            pairs += asr.edits_between(texts[slot], seg["text"])
    _EDIT_CACHE[meeting.id] = (stamp, pairs)
    return pairs


def dictionary_suggestions(core: Core) -> dict:
    from . import asr
    by_meeting = {m.id: meeting_edits(m) for m in list(core.meetings)}
    titles = {m.id: m.title for m in core.meetings}
    items = asr.suggest_from_edits({k: v for k, v in by_meeting.items() if v},
                                   asr.known_pairs(dictionary.load()), dictionary.dismissed())
    for item in items:
        item["meetings"] = [titles[i] for i in item["meetings"] if i in titles][:3]
    return {"items": items}
