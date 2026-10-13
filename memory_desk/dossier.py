"""Досье человека и проекта: всё, что уже извлечено из записей, собранное на одной странице.

Ничего не вычисляется моделью и не пишется в заметки: только чтение базы и заметок. Цифры, которых нельзя
получить честно (например, доля речи без размеченных говорящих), не выдумываются, а возвращаются как None.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from . import knowledge, series, services, storage, voice
from .core import Core

STUCK_WARN_DAYS = 14        # без встреч дольше этого проект подсвечивается
TALK_MEETINGS = 20          # доля речи считается по стольким последним записям человека
MAX_LIST = 12


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _meeting_brief(m: knowledge.Meeting) -> dict:
    project = services.project_dto(m)
    return {"id": m.id, "title": m.title, "date": services.meeting_date(m), "time": m.time, "project": project}


def _open_tasks(rows: list[dict], meta: dict, today: date) -> tuple[list[dict], int]:
    """(открытые и черновые задачи в порядке «срок, потом новизна», из них просроченных)."""
    items = [services._task_dto(r, meta[r["meeting_id"]]["title"], meta[r["meeting_id"]]["meeting_date"])
             for r in rows if r["status"] in {"open", "draft"} and r["meeting_id"] in meta]
    overdue = sum(1 for t in items if t["due"] and _day(t["due"]) and _day(t["due"]) < today)
    items.sort(key=lambda t: (t["due"] or "9999", t["meetingDate"] or ""))
    return items, overdue


def _questions(meetings: list[knowledge.Meeting], limit: int) -> list[dict]:
    out, seen = [], set()
    for m in meetings:
        for fact in knowledge.fact_items(m, "Открытые вопросы"):
            key = series.tokens(fact["text"])
            if any(series.similarity(key, other) >= series.QUESTION_SIMILARITY for other in seen):
                continue
            seen.add(key)
            out.append({"text": fact["text"], "sec": fact["sec"], "meetingId": m.id, "meetingTitle": m.title, "date": services.meeting_date(m)})
            if len(out) >= limit:
                return out
    return out


def _talk(meetings: list[knowledge.Meeting], names: set[str]) -> dict | None:
    """Доля речи человека в записях, где говорящие размечены. None, если разметки нет совсем."""
    mine = total = 0.0
    seen_labeled = False
    for m in meetings[:TALK_MEETINGS]:
        try:
            segments = knowledge.transcript_segments(m)
        except (OSError, ValueError):
            continue
        labeled = [s for s in segments if s.get("speaker")]
        if len(labeled) < 2:
            continue
        seen_labeled = True
        for i, seg in enumerate(labeled):
            nxt = labeled[i + 1]["sec"] if i + 1 < len(labeled) else seg["sec"] + 20
            length = max(0, min(nxt - seg["sec"], 90))
            total += length
            if seg["speaker"].casefold() in names:
                mine += length
    if not seen_labeled or total <= 0:
        return None
    return {"seconds": round(mine), "share": round(mine / total, 3)}


# --------------------------------------------------------------------------- человек

def person(core: Core, person_id: str, today: date | None = None) -> dict:
    today = today or date.today()
    row = storage.one("SELECT * FROM people WHERE id=?", (person_id,))
    if not row:
        raise KeyError("Человек не найден")
    names = services.person_names(row)
    live = core.live_meetings()
    people_map = storage.people_by_meeting()
    mine = [m for m in live if any(p["id"] == person_id for p in people_map.get(m.id, []))]   # от новых к старым
    meta = {r["id"]: r for r in storage.rows("SELECT id,title,meeting_date FROM meetings WHERE archived=0")}

    owner_rows = [r for r in storage.rows("SELECT * FROM tasks WHERE status!='rejected'")
                  if (r.get("owner") or "").casefold() in names and r["meeting_id"] in meta]
    open_tasks, overdue = _open_tasks(owner_rows, meta, today)
    done = sum(1 for r in owner_rows if r["status"] == "done")
    stuck = sum(1 for t in open_tasks if t["stuck"])

    attended = {m.id for m in mine}
    decisions = []
    for d in storage.rows("SELECT d.* FROM decisions d WHERE d.status!='rejected' AND (d.replaced_by IS NULL OR d.replaced_by='') ORDER BY d.rowid DESC"):
        if d["meeting_id"] in attended:
            decisions.append(services._decision_dto(d, meta[d["meeting_id"]]["title"], meta[d["meeting_id"]]["meeting_date"]))
    decisions.sort(key=lambda d: d["meetingDate"] or "", reverse=True)

    by_project: Counter = Counter()
    titles: dict[str, dict] = {}
    for m in mine:
        dto = services.project_dto(m)
        if dto:
            key = dto["id"] or dto["title"]
            by_project[key] += 1
            titles[key] = dto
    projects = [{**titles[k], "meetings": n, "share": round(n / len(mine), 2)} for k, n in by_project.most_common()]

    together: Counter = Counter()
    others: dict[str, dict] = {}
    for m in mine:
        for p in people_map.get(m.id, []):
            if p["id"] != person_id:
                together[p["id"]] += 1
                others[p["id"]] = services.person_dto(p)
    companions = [{**others[pid], "meetings": n} for pid, n in together.most_common(5)]

    dated = [_day(services.meeting_date(m)) for m in mine]
    dated = [d for d in dated if d]
    last = mine[0] if mine else None
    recent = sum(1 for d in dated if (today - d).days <= 30)
    last_day = max(dated) if dated else None
    return {
        "person": {**services.person_dto(row), "voice": voice.all_profile_stats().get(person_id)},
        "stats": {
            "meetings": len(mine), "meetingsLast30": recent,
            "firstContact": min(dated).isoformat() if dated else None, "lastContact": last_day.isoformat() if last_day else None,
            "daysSince": (today - last_day).days if last_day else None,
            "lastMeeting": _meeting_brief(last) if last else None,
            "talk": _talk(mine, names),
        },
        "tasks": {"open": len(open_tasks), "overdue": overdue, "stuck": stuck, "done": done,
                  "drafts": sum(1 for t in open_tasks if t["draft"]), "items": open_tasks[:MAX_LIST]},
        "projects": projects,
        "decisions": {"total": len(decisions), "items": decisions[:MAX_LIST]},
        "questions": _questions(mine[:3], 8),
        "companions": companions,
        "timeline": [_meeting_brief(m) for m in mine[:10]],
    }


# --------------------------------------------------------------------------- проект

PROJECT_STATUSES = {"active": "В работе", "paused": "На паузе", "done": "Завершён"}


def _project_meetings(core: Core, project: dict) -> list[knowledge.Meeting]:
    return [m for m in core.live_meetings() if m.project_id == project["id"] or (not m.project_id and m.project == project["title"])]


def project(core: Core, project_id: str, today: date | None = None) -> dict:
    today = today or date.today()
    found = next((p for p in core.projects() if p["id"] == project_id), None)
    if not found:
        raise KeyError("Проект не найден")
    linked = _project_meetings(core, found)               # от новых к старым
    ids = {m.id for m in linked}
    meta = {r["id"]: r for r in storage.rows("SELECT id,title,meeting_date FROM meetings WHERE archived=0")}
    people_map = storage.people_by_meeting()

    task_rows = [r for r in storage.rows("SELECT * FROM tasks WHERE status!='rejected'") if r["meeting_id"] in ids]
    open_tasks, overdue = _open_tasks(task_rows, meta, today)
    stuck = sum(1 for t in open_tasks if t["stuck"])
    drafts = sum(1 for t in open_tasks if t["draft"])

    full = {r["id"]: r for r in storage.rows("SELECT * FROM people")}
    people: dict[str, dict] = {}
    for m in linked:
        for p in people_map.get(m.id, []):
            row = full.get(p["id"], p)
            entry = people.setdefault(p["id"], {**services.person_dto(row), "meetings": 0, "openTasks": 0})
            entry["meetings"] += 1
    for pid, entry in people.items():
        names = services.person_names(full.get(pid, {"name": entry["name"]}))
        entry["openTasks"] = sum(1 for t in open_tasks if t["owner"].casefold() in names)
    by_owner: dict[str, list[dict]] = {}
    for t in open_tasks:
        by_owner.setdefault(t["owner"] or "Без ответственного", []).append(t)
    tasks_by_person = [{"owner": k, "count": len(v), "items": v[:5]} for k, v in sorted(by_owner.items(), key=lambda kv: -len(kv[1]))]

    active, replaced = [], []
    for d in storage.rows("SELECT * FROM decisions WHERE status!='rejected' ORDER BY rowid DESC"):
        if d["meeting_id"] not in ids:
            continue
        dto = services._decision_dto(d, meta[d["meeting_id"]]["title"], meta[d["meeting_id"]]["meeting_date"])
        (replaced if dto["replacedBy"] else active).append(dto)
    for lst in (active, replaced):
        lst.sort(key=lambda d: d["meetingDate"] or "", reverse=True)

    risks, seen = [], set()
    for m in linked[:5]:
        for fact in knowledge.fact_items(m, "Риски"):
            key = series.tokens(fact["text"])
            if any(series.similarity(key, o) >= series.QUESTION_SIMILARITY for o in seen):
                continue
            seen.add(key)
            risks.append({"text": fact["text"], "sec": fact["sec"], "meetingId": m.id, "meetingTitle": m.title, "date": services.meeting_date(m)})
    questions = _questions(linked[:5], 8)

    dated = sorted(d for d in (_day(services.meeting_date(m)) for m in linked) if d)
    since = (today - dated[-1]).days if dated else None
    monday = today - timedelta(days=today.weekday())
    weeks = []
    for back in range(11, -1, -1):
        start = monday - timedelta(weeks=back)
        weeks.append({"start": start.isoformat(), "meetings": sum(1 for d in dated if start <= d < start + timedelta(days=7))})

    reasons = []
    if overdue:
        reasons.append(f"просрочено задач: {overdue}")
    if stuck:
        reasons.append(f"зависших задач: {stuck}")
    repeated = [s for s in series.listing(linked) if s["repeatedQuestions"]]
    if repeated:
        reasons.append("вопрос висит несколько встреч подряд")
    if found.get("status", "active") == "active" and since is not None and since > STUCK_WARN_DAYS:
        reasons.append(f"встреч не было {since} дн.")
    level = "bad" if overdue >= 3 or (overdue and stuck) else "warn" if reasons else "ok"

    stored = storage.one("SELECT description FROM projects WHERE id=?", (project_id,)) or {}
    return {
        "project": {"id": found["id"], "title": found["title"], "color": services.color_for("project:" + found["id"]),
                    "status": found.get("status") or "active", "statusLabel": PROJECT_STATUSES.get(found.get("status") or "active", found.get("status")),
                    "goal": found.get("goal") or "", "description": stored.get("description") or ""},
        "health": {"level": level, "reasons": reasons, "overdue": overdue, "stuck": stuck, "drafts": drafts,
                   "openTasks": len(open_tasks), "openQuestions": len(questions), "daysSinceLast": since},
        "counts": {"records": len(linked), "first": dated[0].isoformat() if dated else None, "last": dated[-1].isoformat() if dated else None},
        "people": sorted(people.values(), key=lambda p: -p["meetings"]),
        "tasksByPerson": tasks_by_person,
        "decisions": {"active": active[:MAX_LIST], "activeTotal": len(active), "replaced": replaced[:MAX_LIST], "replacedTotal": len(replaced)},
        "risks": risks[:8], "questions": questions,
        "weeks": weeks,
        "series": [s for s in series.listing(linked) if s["count"] > 1][:6],
        "recent": [_meeting_brief(m) for m in linked[:8]],
    }
