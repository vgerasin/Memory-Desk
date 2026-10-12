"""Сборка данных для интерфейса: DTO по сущностям из дизайн-референса (Meeting, Job, Task, ...)."""
from __future__ import annotations

import hashlib
import re
import shutil
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, digest, integrations, jobs, knowledge, speakers as speakers_mod, storage, voice
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
    return {"id": person["id"], "name": person["name"], "role": person.get("role") or "",
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
    }


def _decision_dto(row: dict, meeting_title: str = "", meeting_date_value: str = "") -> dict:
    t = row.get("evidence_time") or ""
    return {
        "id": row["id"], "text": row["text"], "owner": (row.get("owner") or "").strip(), "t": t,
        "tSec": clock_seconds(t), "status": row["status"], "draft": row["status"] == "draft",
        "meetingId": row["meeting_id"], "meetingTitle": meeting_title, "meetingDate": meeting_date_value,
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
            "goal": project.get("goal") or stored.get("goal") or stored.get("description") or "",
            "counts": {"records": len(linked), "openTasks": open_tasks, "questions": questions},
            "people": list(people.values()), "lastChange": change,
        })
    return result


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
        name_cf = person["name"].casefold()
        first = name_cf.split()[0] if name_cf.split() else name_cf
        owned = [t for t in tasks if (t.get("owner") or "").casefold() in {name_cf, first}]
        projects = list(dict.fromkeys(l["project_title"] for l in mine if l["project_title"] and l["project_title"] != NO_PROJECT))
        last = _task_dto(owned[0], owned[0]["mt"], owned[0]["md"]) if owned else None
        result.append({**person_dto(person), "meetings": len(mine), "projects": projects,
                       "obligations": len(owned), "lastAgreement": last, "voice": voices.get(person["id"])})
    return result


def tasks_registry(core: Core, kind: str, limit: int = 0, offset: int = 0) -> dict:
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
        total = len(raw)
        raw = raw[offset:offset + limit] if limit else raw
        items = [_task_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"]) for r in raw]
    counts = {
        "tasks": storage.one("SELECT COUNT(*) n FROM tasks t JOIN meetings m ON m.id=t.meeting_id WHERE t.status!='rejected' AND m.archived=0")["n"],
        "decisions": storage.one("SELECT COUNT(*) n FROM decisions d JOIN meetings m ON m.id=d.meeting_id WHERE d.status!='rejected' AND m.archived=0")["n"],
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
        "ORDER BY m.recorded_at DESC, d.rowid") if in_scope(r["meeting_id"])][:3]
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
                                       "AND m.archived=0 ORDER BY m.recorded_at DESC, d.rowid") if r["meeting_id"] in ids][:6]

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
    sections = [
        {"key": "changes", "title": "Что менялось", "empty": "Записей пока нет", "items": changes},
        {"key": "decisions", "title": "Решения", "empty": "Решений пока нет", "items": decisions},
        {"key": "tasks", "title": "Открытые задачи", "empty": "Открытых задач нет", "items": tasks},
        {"key": "questions", "title": "Открытые вопросы", "empty": "Открытых вопросов нет", "items": questions[:6]},
        {"key": "risks", "title": "Риски", "empty": "Рисков не отмечено", "items": risks[:4]},
    ]
    return {"title": title, "projectId": project_id, "meetingId": meeting_id, "records": len(scope), "sections": sections}


# --------------------------------------------------------------------------- аналитика

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


def chat(core: Core, question: str, scope: dict, previous: list[str]) -> dict:
    question = question.strip()
    if not question:
        raise ValueError("Вопрос не может быть пустым")
    summary = digest.build(core, question, scope, meeting_date)
    if summary is not None:
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
    return {"answer": answer, "sources": grouped[:4], "mode": mode, "confirmed": bool(sources)}


# --------------------------------------------------------------------------- система

def _ollama_status() -> tuple[str, str]:
    try:
        import json
        with urllib.request.urlopen(f"{config.OLLAMA_URL}/api/tags", timeout=0.6) as response:
            tags = json.load(response)
        names = [m.get("name", "") for m in tags.get("models", [])]
        if any(n.startswith(config.OLLAMA_MODEL.split(":")[0]) for n in names):
            return config.OLLAMA_MODEL, "ok"
        return "модель не загружена", "warn"
    except Exception:  # noqa: BLE001
        return "не отвечает", "warn"


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
        {"key": "backup", "name": "Резервная копия",
         "value": datetime.fromtimestamp(backups[0].stat().st_mtime).strftime("%d.%m, %H:%M") if backups else "нет копии",
         "state": "ok" if backups else "warn"},
    ]
    order = {"ok": 0, "warn": 1, "bad": 2}
    overall = max((r["state"] for r in rows), key=lambda s: order[s])
    return {"rows": rows, "overall": overall, "indexState": index.state, "indexError": index.error}
