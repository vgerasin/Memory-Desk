"""Дайджест по проектам за неделю или месяц: документ с таймкодами и презентация.

Берутся записи периода (по всем проектам или по одному). По каждому проекту: решения, новые задачи, закрытое за период,
то, что требует внимания (просроченное, в том числе из прежних записей), риски и открытые вопросы. Все пункты берутся из
извлечённых фактов и несут таймкод места в записи, как в служебной записке и брифе (`artifacts.py`). Модель пишет только
вводный абзац «Главное»; без неё он собирается из цифр. Закрытым за период считается задача со статусом «выполнена»,
дата последней правки которой попадает в период."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from . import artifacts, digest, knowledge, materials

PERIODS = {"this_week": "Эта неделя", "last_week": "Прошлая неделя", "this_month": "Этот месяц", "last_month": "Прошлый месяц"}
STATUS_LABELS = {"active": "В работе", "paused": "На паузе", "done": "Завершён"}
NO_PROJECT = "Без проекта"
MAX_PROJECTS = 12
LIMIT = 6


def period_range(key: str, today: date) -> tuple[date, date, str]:
    """Границы периода по календарю (неделя с понедельника) и подпись."""
    if key not in PERIODS:
        raise ValueError("Период: неделя или месяц (эта или прошлая)")
    if key in ("this_week", "last_week"):
        monday = today - timedelta(days=today.weekday())
        if key == "last_week":
            monday -= timedelta(days=7)
            since, until = monday, monday + timedelta(days=6)
        else:
            since, until = monday, today
        ru = knowledge.RU_MONTHS
        if since.month == until.month:
            label = f"Неделя {since.day}–{until.day} {ru[until.month]} {until.year}"
        else:
            label = f"Неделя {since.day} {ru[since.month]} – {until.day} {ru[until.month]} {until.year}"
        return since, until, label
    if key == "this_month":
        since, until = today.replace(day=1), today
    else:
        until = today.replace(day=1) - timedelta(days=1)
        since = until.replace(day=1)
    return since, until, f"{digest.MONTH_NAMES[until.month].capitalize()} {until.year}"


def period_from_text(text: str, today: date) -> str:
    """Период дайджеста по фразе: «месячный» или «недельный», «прошлый» или «этот»; без уточнения — последний законченный
    период в начале недели (месяца) и текущий дальше."""
    import re
    low = text.casefold()
    month = bool(re.search(r"месяц|месячн", low))
    if re.search(r"прошл|прошедш|предыдущ", low):
        last = True
    elif re.search(r"\bэт[аоуи]\w*|текущ", low):
        last = False
    else:
        last = today.day <= 3 if month else today.weekday() <= 1
    return ("last_" if last else "this_") + ("month" if month else "week")


@dataclass
class Group:
    id: str
    title: str
    status: str
    meetings: list
    gathered: artifacts.Gathered = field(default_factory=artifacts.Gathered)
    closed: list = field(default_factory=list)
    attention: list = field(default_factory=list)

    @property
    def decisions(self): return self.gathered.of("decision")

    @property
    def new_tasks(self):
        due = {(i.meeting.id, i.text) for i in self.attention}
        return [i for i in self.gathered.of("task") if (i.meeting.id, i.text) not in due]

    @property
    def risks(self): return self.gathered.of("risk")

    @property
    def questions(self): return self.gathered.of("question")

    def counts(self) -> dict:
        return {"records": len(self.meetings), "decisions": len(self.decisions), "newTasks": len(self.new_tasks),
                "closed": len(self.closed), "overdue": len(self.attention)}


@dataclass
class Digest:
    key: str
    since: date
    until: date
    label: str
    title: str
    scope: str
    groups: list[Group]
    today: date
    skipped: list = field(default_factory=list)
    quiet: list = field(default_factory=list)      # активные проекты без записей в периоде: (название, дата последней записи)

    @property
    def meetings(self) -> list:
        return [m for g in self.groups for m in g.meetings]

    def totals(self) -> dict:
        totals = {"records": 0, "decisions": 0, "newTasks": 0, "closed": 0, "overdue": 0}
        for g in self.groups:
            for k, v in g.counts().items():
                totals[k] += v
        return totals


def collect(core, key: str, date_of: Callable, today: date | None = None, project_id: str | None = None) -> Digest:
    today = today or date.today()
    since, until, label = period_range(key, today)
    projects = core.projects()
    project = None
    if project_id:
        project = next((p for p in projects if p["id"] == project_id), None)
        if not project:
            raise KeyError("Проект не найден")
    intent = digest.Intent(since, until, label, True, project, None)
    meetings = digest.select_meetings(core, intent, date_of)
    if not meetings:
        raise ValueError(f"За период «{label}» записей нет")
    by_key = {(p["id"], p["title"]): p for p in projects}
    groups: dict[str, Group] = {}
    for m in meetings:
        found = next((p for (pid, title), p in by_key.items() if (m.project_id and m.project_id == pid) or (m.project and m.project == title)), None)
        gid = found["id"] if found else ""
        group = groups.setdefault(gid, Group(gid, found["title"] if found else (m.project or NO_PROJECT), found.get("status", "active") if found else "", []))
        group.meetings.append(m)
    live = core.live_meetings()
    skipped: list = []
    for group in groups.values():
        group.gathered = artifacts.gather(group.meetings, date_of, today, kinds={"decision", "task", "risk", "question"})
        history = [m for m in live if group.id and (m.project_id == group.id or m.project == group.title)] or group.meetings
        everything = artifacts.gather(history, date_of, today, kinds={"task"})
        group.attention = sorted((i for i in everything.items if i.overdue), key=lambda i: (i.due or "9999", i.text))
        finished = artifacts.gather(history, date_of, today, kinds={"done"}, task_status=("done",))
        group.closed = [i for i in finished.items if since.isoformat() <= i.updated <= until.isoformat()]
        skipped += group.gathered.skipped
    ordered = sorted(groups.values(), key=lambda g: (g.id == "" and g.title == NO_PROJECT, -len(g.meetings), g.title.casefold()))
    scope = project["title"] if project else "все проекты"
    quiet = []
    if not project:
        for p in projects:
            if p["id"] in groups or p.get("status", "active") != "active":
                continue
            days = [date_of(m) for m in live if m.project_id == p["id"] or m.project == p["title"]]
            quiet.append((p["title"], max((x for x in days if x), default="")))
    return Digest(key, since, until, label, f"{label} · {scope}", scope, ordered, today, skipped, quiet)


# --------------------------------------------------------------------------- вводный абзац

def _lead(d: Digest) -> tuple[str, str]:
    t = d.totals()
    n = len(d.groups)
    fallback = (f"За период: {digest._plural(t['records'], 'запись', 'записи', 'записей')} по "
                f"{digest._plural(n, 'проекту', 'проектам', 'проектам')}, {digest._plural(t['decisions'], 'решение', 'решения', 'решений')}, "
                f"новых задач {t['newTasks']}, закрыто {t['closed']}, просрочено {t['overdue']}.")
    items = [i for g in d.groups for i in g.decisions + g.attention + g.risks]
    return artifacts._lead("Напиши итог периода для руководителя: что решили и что требует внимания.", items, fallback)


# --------------------------------------------------------------------------- документ

def _group_markdown(g: Group) -> list[str]:
    c = g.counts()
    state = (STATUS_LABELS.get(g.status, "") + ". ") if g.status else ""
    lines = ["", f"## {g.title}", "",
             f"_{state}Записей: {c['records']}, решений: {c['decisions']}, новых задач: {c['newTasks']}, закрыто: {c['closed']}, просрочено: {c['overdue']}._"]

    def section(title: str, rows: list[str]) -> None:
        if rows:
            lines.extend(["", f"### {title}", ""])
            artifacts._emit(lines, rows, "")

    section("Решения", [artifacts._decision(i, True, True) for i in g.decisions])
    section("Требует внимания", [artifacts._task(i, True, True, "я") for i in g.attention])
    section("Новые задачи", [artifacts._task(i, True, True, "я") for i in g.new_tasks])
    section("Закрыто за период", [f"- {i.text}" + (f" ({i.owner})" if i.owner else "") + f" — {artifacts._where(i, True, True)}" for i in g.closed])
    section("Риски", [artifacts._plain(i, True, True) for i in g.risks])
    section("Открытые вопросы", [artifacts._plain(i, True, True) for i in g.questions])
    return lines


def markdown(d: Digest) -> tuple[str, str]:
    """Текст дайджеста и режим вводного абзаца."""
    t = d.totals()
    lead, mode = _lead(d)
    lines = [f"# Дайджест: {d.title}", "", f"**Период:** {digest.short(d.since.isoformat())} – {digest.short(d.until.isoformat())}",
             f"**Проекты:** {d.scope}", f"**Записей:** {t['records']}", "", "## Главное", "", lead]
    if len(d.groups) > 1:
        lines += ["", "| Проект | Записей | Решений | Новых задач | Закрыто | Просрочено |", "| --- | --- | --- | --- | --- | --- |"]
        for g in d.groups:
            c = g.counts()
            lines.append(f"| {artifacts._cell(g.title)} | {c['records']} | {c['decisions']} | {c['newTasks']} | {c['closed']} | {c['overdue']} |")
    for g in d.groups:
        lines += _group_markdown(g)
    quiet = _quiet(d)
    if quiet:
        lines += ["", "## Без записей за период", "", quiet]
    foot = [""]
    if d.skipped:
        foot += [f"_Не включено пунктов без привязки к месту в записи: {len(d.skipped)}._", ""]
    foot.append(f"_Составлено автоматически из записей ({t['records']}) Memory Desk; проверьте по таймкодам перед использованием._")
    return "\n".join(lines + foot) + "\n", mode


def _quiet(d: Digest) -> str:
    """Активные проекты без записей за период (абзац без пунктов: это не факты из записей, а их отсутствие)."""
    return ", ".join(f"{title} (последняя запись {digest.short(last)})" if last else f"{title} (записей нет)" for title, last in d.quiet)


# --------------------------------------------------------------------------- вход

def build(core, key: str, date_of: Callable, today: date | None = None, project_id: str | None = None) -> dict:
    d = collect(core, key, date_of, today, project_id)
    text, mode = markdown(d)
    return {
        "kind": "digest", "title": f"Дайджест: {d.title}", "markdown": text, "mode": mode, "records": len(d.meetings),
        "date": d.until.isoformat(), "period": {"key": d.key, "since": d.since.isoformat(), "until": d.until.isoformat(), "label": d.label},
        "fileName": materials.safe_name(f"{d.until.isoformat()} — Дайджест — {d.title}") + ".md",
        "refs": [{"id": m.id, "title": m.title, "label": artifacts._label(m.title)} for m in d.meetings],
        "projects": [{"id": g.id, "title": g.title, **g.counts()} for g in d.groups],
        "count": sum(len(g.decisions) + len(g.new_tasks) + len(g.attention) + len(g.closed) + len(g.risks) + len(g.questions) for g in d.groups),
        "skipped": list(d.skipped),
    }


def build_deck(core, key: str, date_of: Callable, today: date | None = None, project_id: str | None = None) -> dict:
    d = collect(core, key, date_of, today, project_id)
    lead, mode = _lead(d)
    t = d.totals()
    deck = materials.Slides(d.today)
    deck.cover(f"Дайджест: {d.label}", d.scope[:1].upper() + d.scope[1:], digest._plural(t["records"], "запись", "записи", "записей") + f", {digest._plural(len(d.groups), 'проект', 'проекта', 'проектов')}")
    numbers = [] if mode == "structured" else [f"Решений: {t['decisions']}, новых задач: {t['newTasks']}, закрыто: {t['closed']}, просрочено: {t['overdue']}"]
    deck.bullets("Главное", [lead] + numbers, 7,
                 f"{digest.short(d.since.isoformat())} – {digest.short(d.until.isoformat())}")
    if len(d.groups) > 1:
        rows = []
        for g in d.groups[:MAX_PROJECTS * 2]:
            c = g.counts()
            rows.append((materials._clip(g.title, 40), str(c["records"]), str(c["decisions"]), str(c["newTasks"]), str(c["closed"]), str(c["overdue"])))
        deck.table("Проекты", ("Проект", "Записей", "Решений", "Новых задач", "Закрыто", "Просрочено"), rows, (4.4, 1.4, 1.5, 1.8, 1.4, 1.4))
    for g in d.groups[:MAX_PROJECTS]:
        c = g.counts()
        items = ([f"Решение: {i.text}" for i in g.decisions[:3]] + [f"Внимание: {i.text}" + (f" — {i.owner}" if i.owner else "") for i in g.attention[:3]]
                 + [f"Новая задача: {i.text}" + (f" — {i.owner}" if i.owner else "") for i in g.new_tasks[:2]]
                 + [f"Закрыто: {i.text}" for i in g.closed[:2]] + [f"Вопрос: {i.text}" for i in g.questions[:2]])
        deck.bullets(g.title, items or ["За период нет решений и задач."], 7,
                     f"Записей: {c['records']} · решений: {c['decisions']} · новых задач: {c['newTasks']} · закрыто: {c['closed']} · просрочено: {c['overdue']}")
    overdue = [(materials._clip(i.text, 70), i.owner or "не указан", digest.short(i.due) if i.due else "—", g.title[:28]) for g in d.groups for i in g.attention]
    if overdue:
        deck.table("Требует внимания", ("Задача", "Ответственный", "Срок", "Проект"), overdue, (5.4, 2.4, 1.6, 2.5))
    name = materials.safe_name(f"Дайджест — {d.title} — {d.today.isoformat()}", 90) + ".pptx"
    slides = deck.save(materials._presentations_dir() / name)
    return {"name": name, "title": f"Дайджест: {d.title}", "period": d.label, "slides": slides, "records": len(d.meetings), "mode": mode,
            "url": materials.deck_url(name)}
