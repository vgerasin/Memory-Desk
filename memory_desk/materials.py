"""Материалы по запросу: протокол встречи и карта (mind map). Презентации .pptx убраны в этапе 14.

Все материалы собираются из уже извлечённых фактов (краткое содержание, решения, задачи со статусами, риски,
вопросы), поэтому работают и без модели. Локальная модель, если она доступна, добавляет связный текст
(«Ход обсуждения» в протоколе); при её недоступности материал остаётся полным."""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import attachments, bookmarks, digest, knowledge, storage

MAX_ITEMS = 8


# --------------------------------------------------------------------------- область

@dataclass
class Scope:
    title: str
    label: str
    meetings: list
    intent: digest.Intent | None = None
    single: bool = False


def resolve(core, scope: dict, date_of: Callable, today: date | None = None) -> Scope:
    """Область материала: одна запись, проект, человек и/или период (по умолчанию 30 дней)."""
    today = today or date.today()
    meeting_id = scope.get("meetingId")
    if meeting_id:
        meeting = core.by_id.get(meeting_id)
        if not meeting:
            raise KeyError("Запись не найдена")
        return Scope(meeting.title, date_of(meeting) or "", [meeting], None, True)
    project = person = None
    if scope.get("projectId"):
        project = next((p for p in core.projects() if p["id"] == scope["projectId"]), None)
        if not project:
            raise KeyError("Проект не найден")
    if scope.get("personId"):
        rows = storage.rows("SELECT id,name FROM people WHERE id=?", (scope["personId"],))
        if not rows:
            raise KeyError("Человек не найден")
        person = rows[0]
    if scope.get("since") and scope.get("until"):
        try:
            since, until = date.fromisoformat(str(scope["since"])), date.fromisoformat(str(scope["until"]))
        except ValueError:
            raise ValueError("Даты периода должны быть в формате ГГГГ-ММ-ДД") from None
        if since > until:
            since, until = until, since
        label = scope.get("label") or f"{digest.short(since.isoformat())} – {digest.short(until.isoformat())}"
        intent = digest.Intent(since, until, label, True, project, person)
    else:
        try:
            days = min(max(int(scope.get("days") or 30), 1), 3650)
        except (TypeError, ValueError):
            raise ValueError("Период должен быть числом дней") from None
        label = "всё время" if days >= 3650 else f"последние {days} дн."
        intent = digest.Intent(today - timedelta(days=days), today, label, True, project, person)
    meetings = digest.select_meetings(core, intent, date_of)
    if intent.label == "всё время" and meetings:  # в заголовке — реальные границы архива, а не «десять лет назад»
        dates = [d for d in (date_of(m) for m in meetings) if d]
        if dates:
            intent.since, intent.until = date.fromisoformat(min(dates)), date.fromisoformat(max(dates))
    return Scope(digest.title_line(intent), intent.label, meetings, intent)


def _clip(text: str, limit: int = 140) -> str:
    return digest._clean(text, limit)


def _ru_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {knowledge.RU_MONTHS[d.month]} {d.year}"


def _due(task: dict) -> str:
    if task["due"]:
        return digest.short(task["due"])
    return task["dueText"] or "не указан"


# --------------------------------------------------------------------------- протокол

def protocol(core, meeting_id: str, date_of: Callable, today: date | None = None,
             progress: Callable[..., None] | None = None) -> dict:
    """Протокол записи. `progress(этап, подпись, процент, запас, постоянная)` сообщает ход работы: пока идёт запрос
    к модели, процент плавно растёт от `процент` на `запас` пунктов (время ответа модели заранее неизвестно)."""
    note = progress or (lambda *a, **k: None)
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise KeyError("Запись не найдена")
    note(1, "Собираю факты записи", 5)
    f = digest.gather([meeting], date_of, today)[0]
    people = [p["name"] for p in storage.people_by_meeting().get(meeting.id, [])]
    topics = [x["text"] for x in knowledge.fact_items(meeting, "Основные темы")] or []
    lines = [f"# Протокол: {meeting.title}", ""]
    when = _ru_date(f.date) + (f", {meeting.time}" if meeting.time else "")
    lines.append(f"**Дата:** {when}" + (f" · **Длительность:** {meeting.duration}" if meeting.duration else ""))
    meta = [x for x in (meeting.project, meeting.source_type if meeting.source_type not in ("", "Не указан") else "") if x]
    if meta:
        lines.append("**Проект и тип:** " + " · ".join(meta))
    lines.append("**Участники:** " + (", ".join(people) if people else "не указаны"))
    if topics:
        lines += ["", "## Повестка и темы", ""] + [f"- {_clip(t, 200)}" for t in topics[:MAX_ITEMS]]

    mode = "structured"
    discussion = ""
    summary = list(dict.fromkeys(f.summary))  # повторяющиеся пункты резюме не нужны ни модели, ни читателю
    if summary:
        note(2, "Локальная модель пишет ход обсуждения", 15, 72, 14)
        body = "\n".join("- " + s for s in summary)
        prompt = ("Ты помощник по протоколам встреч. Напиши ход обсуждения в 3–5 предложениях, только по пунктам ниже, "
                  "ничего не выдумывай, без вступлений и списков. По-русски.\n\nПункты:\n" + body + "\n\nХОД ОБСУЖДЕНИЯ:\n")
        try:
            discussion = digest.generate(prompt, 350).strip()
            mode = "model" if discussion else "structured"
        except OSError:
            discussion = ""
            note(2, "Модель недоступна: ход обсуждения берётся из резюме", 87)
    lines += ["", "## Ход обсуждения", ""]
    note(3, "Оформляю решения, поручения, риски и вопросы", 90)
    if discussion:
        lines.append(discussion)
    elif summary:
        lines += [f"- {s}" for s in summary]
    else:
        lines.append("Краткое содержание не заполнено.")

    lines += ["", "## Решения", ""]
    lines += [f"{i}. {d['text']}{' _(не подтверждено)_' if d['draft'] else ''}{' _(позднее заменено)_' if d.get('replaced') else ''}" for i, d in enumerate(f.decisions, 1)] or ["Решений не зафиксировано."]

    lines += ["", "## Поручения", ""]
    tasks = [(t, "в работе") for t in f.open] + [(dict(t, due="", dueText="", overdue=False, draft=False), "выполнено") for t in f.done]
    if tasks:
        lines += ["| № | Поручение | Ответственный | Срок | Статус |", "| --- | --- | --- | --- | --- |"]
        for i, (t, state) in enumerate(tasks, 1):
            status = state + (", просрочено" if t.get("overdue") else "") + (", черновик" if t.get("draft") else "")
            cell = lambda v: str(v).replace("|", "/")  # noqa: E731
            lines.append(f"| {i} | {cell(t['text'])} | {cell(t['owner'] or 'не указан')} | {cell(_due(t) if state == 'в работе' else '—')} | {status} |")
    else:
        lines.append("Поручений не зафиксировано.")

    if f.risks:
        lines += ["", "## Риски", ""] + [f"- {r}" for r in f.risks]
    if f.questions:
        lines += ["", "## Открытые вопросы", ""] + [f"- {q}" for q in f.questions]
    marks = bookmarks.list_for(meeting)
    if marks:
        lines += ["", "## Отмечено на записи", ""] + [f"- `⏱ {m['t']}`" + (f" — {m['text']}" if m["text"] else "") for m in marks[:MAX_ITEMS]]
    files = attachments.notes_for(meeting, 600)
    if files:
        lines += ["", "## Материалы к записи", ""]
        for item in files[:MAX_ITEMS]:
            kind = "фото" if item["kind"] == "image" else "текст"
            lines.append(f"- **{item['name']}** ({kind}{', черновик распознавания' if item['draft'] else ''}): "
                         + " ".join(item["text"].split()))
    lines += ["", f"_Составлено автоматически из записи «{meeting.title}» ({digest.short(f.date)}); проверьте перед рассылкой._"]
    note(4, "Проверяю текст", 97)
    return {"title": f"Протокол: {meeting.title}", "markdown": "\n".join(lines) + "\n", "mode": mode, "meetingId": meeting.id,
            "date": f.date, "fileName": safe_name(f"{f.date} — {meeting.title} — протокол") + ".md"}


# --------------------------------------------------------------------------- ход составления протокола

PROTOCOL_STEPS = 4
PROTOCOL_JOBS: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _percent(job: dict, now: float) -> int:
    if job["state"] == "done":
        return 100
    value = job["base"]
    if job["span"] and job["tau"]:
        value += job["span"] * (1 - math.exp(-max(0.0, now - job["stageStarted"]) / job["tau"]))
    return min(99, int(value))


def protocol_progress(meeting_id: str) -> dict:
    """Состояние составления протокола для интерфейса: этап, процент, прошедшее время; по готовности — сам протокол."""
    job = PROTOCOL_JOBS.get(meeting_id)
    if not job:
        return {"state": "idle"}
    now = time.time()
    view = {"state": job["state"], "step": job["step"], "steps": PROTOCOL_STEPS, "label": job["label"],
            "percent": _percent(job, now), "elapsed": int((job.get("finishedAt") or now) - job["startedAt"])}
    if job["state"] == "done":
        view["result"] = job["result"]
    if job["state"] == "error":
        view["message"] = job["message"]
        view["status"] = job.get("status", 500)
    return view


def start_protocol(core, meeting_id: str, date_of: Callable, today: date | None = None) -> dict:
    """Запускает составление протокола в фоне. Уже идущее для этой записи повторно не запускается."""
    if meeting_id not in core.by_id:
        raise KeyError("Запись не найдена")
    with _jobs_lock:
        current = PROTOCOL_JOBS.get(meeting_id)
        if current and current["state"] == "running":
            return protocol_progress(meeting_id)
        now = time.time()
        job = {"state": "running", "step": 1, "label": "Начинаю", "base": 2, "span": 0, "tau": 0,
               "stageStarted": now, "startedAt": now}
        PROTOCOL_JOBS[meeting_id] = job

    def note(step: int, label: str, base: float, span: float = 0, tau: float = 0) -> None:
        job.update(step=step, label=label, base=base, span=span, tau=tau, stageStarted=time.time())

    def work() -> None:
        try:
            job["result"] = protocol(core, meeting_id, date_of, today, progress=note)
            job["state"] = "done"
        except KeyError as exc:
            job.update(state="error", message=str(exc.args[0]), status=404)
        except Exception as exc:  # noqa: BLE001 — причину должен увидеть пользователь
            job.update(state="error", message=f"Не удалось составить протокол: {exc}", status=500)
        finally:
            job["finishedAt"] = time.time()

    threading.Thread(target=work, name="protocol", daemon=True).start()
    return protocol_progress(meeting_id)


def safe_name(value: str, limit: int = 80) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", " ".join(str(value).split())).strip(" .-")
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0].strip(" .-,;") if " " in cleaned[:limit] else cleaned[:limit]
    return cleaned or "Материал"


FOLDERS = {"protocol": "Протоколы", "map": "Карты", "memo": "Служебные записки", "brief": "Брифы", "followup": "Письма", "digest": "Дайджесты"}


def save_text(kind: str, file_name: str, content: str) -> Path:
    """Атомарно сохраняет материал в рабочую папку Obsidian: Протоколы/, Карты/, Служебные записки/, Брифы/ или Письма/. Имя очищается от служебных символов."""
    if kind not in FOLDERS:
        raise ValueError("Неизвестный вид материала")
    folder = knowledge.WORKSPACE_DIR / FOLDERS[kind]
    folder.mkdir(parents=True, exist_ok=True)
    stem = safe_name(re.sub(r"\.md$", "", str(file_name), flags=re.I))
    target = folder / (stem + ".md")
    temp = target.with_name("." + target.name + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(target)
    return target


def save_protocol(result: dict) -> Path:
    """Сохраняет протокол рядом с заметками: <рабочая папка Obsidian>/Протоколы/."""
    return save_text("protocol", result["fileName"], result["markdown"])


# --------------------------------------------------------------------------- карта

def _node_text(text: str, limit: int = 90) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[()\[\]{}\"`#<>|]", " ", _clip(text, limit))).strip() or "—"


def _branches(f: digest.MeetingFacts) -> list[dict]:
    out = []
    if f.decisions:
        out.append({"text": "Решения", "children": [{"text": d["text"] + (" (не подтверждено)" if d["draft"] else "") + (" (заменено)" if d.get("replaced") else ""), "children": []} for d in f.decisions[:MAX_ITEMS]]})
    open_items = [{"text": t["text"] + (f" — {t['owner']}" if t["owner"] else "") + (f", до {_due(t)}" if t["due"] or t["dueText"] else "")
                   + (" (просрочено)" if t["overdue"] else ""), "children": []} for t in f.open[:MAX_ITEMS]]
    done_items = [{"text": "✓ " + t["text"], "children": []} for t in f.done[:MAX_ITEMS]]
    if open_items or done_items:
        out.append({"text": "Задачи", "children": open_items + done_items})
    if f.risks:
        out.append({"text": "Риски", "children": [{"text": r, "children": []} for r in f.risks]})
    if f.questions:
        out.append({"text": "Вопросы", "children": [{"text": q, "children": []} for q in f.questions]})
    return out


def mindmap(core, scope: dict, date_of: Callable, today: date | None = None) -> dict:
    sc = resolve(core, scope, date_of, today)
    if not sc.meetings:
        raise ValueError("За выбранный период записей нет")
    facts = digest.gather(sc.meetings, date_of, today)
    if sc.single:
        children = _branches(facts[0])
    else:
        children = []
        for f in facts[-12:]:
            branches = _branches(f)
            if branches:
                children.append({"text": f"{digest.short(f.date)} · {f.title}", "children": branches})
    tree = {"text": sc.title, "children": children}
    lines = ["mindmap", f"  root(({_node_text(tree['text'], 60)}))"]
    counter = [0]

    def walk(node: dict, depth: int) -> None:
        for child in node["children"]:
            counter[0] += 1
            lines.append("  " * (depth + 1) + f"n{counter[0]}[{_node_text(child['text'])}]")
            walk(child, depth + 1)

    walk(tree, 1)
    return {"title": sc.title, "tree": tree, "mermaid": "\n".join(lines), "records": len(sc.meetings)}


def mindmap_note(result: dict) -> str:
    return f"# Карта: {result['title']}\n\n```mermaid\n{result['mermaid']}\n```\n"


def tree_text(tree: dict) -> str:
    """Дерево отступами — для ответа в чате."""
    out = [f"**{tree['text']}**"]

    def walk(node: dict, depth: int) -> None:
        for child in node["children"]:
            out.append("  " * depth + "- " + child["text"])
            walk(child, depth + 1)

    walk(tree, 0)
    return "\n".join(out)


# --------------------------------------------------------------------------- запрос из чата

VERBS = re.compile(r"сдела|созда|постро|нарису|подготов|состав|сформир|сгенерир|собери|нужн|хочу|выгрузи|оформи|оформл|дай\b|пришли", re.IGNORECASE)
KINDS = (("memo", re.compile(r"служебн\w*\s+записк|\bзаписк[ауие]\s+(?:руковод|начальник|директор)", re.IGNORECASE)),
         ("brief", re.compile(r"\bбриф", re.IGNORECASE)),
         ("followup", re.compile(r"follow[- ]?up|фолл?оу[- ]?ап|письм\w*\s+(?:участник|с\s+итог|по\s+итог)|итогов\w+\s+письм", re.IGNORECASE)),
         ("protocol", re.compile(r"протокол", re.IGNORECASE)),
         ("map", re.compile(r"\bкарт[ауыеой]\w*|mind ?map|майнд|интеллект[- ]карт", re.IGNORECASE)))


def request_kind(question: str) -> str | None:
    """Просит ли вопрос создать материал («составь протокол», «построй карту»)."""
    if not VERBS.search(question):
        return None
    if re.search(r"дайджест", question, re.IGNORECASE):
        return "digest"
    return next((kind for kind, pattern in KINDS if pattern.search(question)), None)


def chat_markdown(markdown: str) -> str:
    """Markdown протокола -> мини-разметка чата: заголовки жирным, строки таблицы списком."""
    out = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"\|?[\s:\-|]+\|?", stripped) and "-" in stripped:
            continue
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells and cells[0] in ("№", "#", "Поручение"):
                continue
            out.append("- " + " — ".join(c for c in cells if c))
            continue
        heading = re.match(r"#{1,6}\s+(.*)", stripped)
        out.append(f"**{heading.group(1)}**" if heading else line)
    return "\n".join(out)


def from_chat(core, question: str, scope: dict, date_of: Callable, today: date | None = None) -> dict | None:
    """Ответ чата на просьбу создать материал или None, если вопрос обычный. Протокол и карта возвращаются текстом
    с кнопкой «Открыть окно» (правка, копирование, Obsidian)."""
    kind = request_kind(question)
    if kind is None:
        return None
    today = today or date.today()
    scope = scope or {"kind": "all"}
    project, person = digest.subject(core, question, scope)
    period = digest.parse_period(question, today)
    payload: dict = {"projectId": project["id"] if project else None, "personId": person["id"] if person else None}
    if period:
        payload.update(since=period[0].isoformat(), until=period[1].isoformat(), label=period[2])
    else:
        payload["days"] = 3650 if (project or person) else 30
    try:
        if kind == "digest":
            from . import projectdigest
            key = projectdigest.period_from_text(question, today)
            project_id = project["id"] if project else None
            result = projectdigest.build(core, key, date_of, today, project_id)
            answer = chat_markdown(result["markdown"])
        elif kind in ("memo", "brief", "followup"):
            from . import artifacts
            payload["meetingId"] = scope.get("id") if scope.get("kind") == "record" else None
            if not payload["meetingId"] and not (project or person or period):
                payload["days"] = 7
            result = artifacts.build(core, kind, payload, date_of, today)
            answer = chat_markdown(result["markdown"])
        elif kind == "protocol":
            meeting_id = scope.get("id") if scope.get("kind") == "record" else None
            note = ""
            if not meeting_id:
                found = resolve(core, payload, date_of, today).meetings
                if not found:
                    raise ValueError("Не нашёл записей для протокола. Откройте нужную запись и нажмите «Протокол» или выберите область «Текущая запись».")
                meeting_id = found[-1].id
                note = f"Беру последнюю подходящую запись: «{found[-1].title}».\n\n"
            result = protocol(core, meeting_id, date_of, today)
            result["records"] = 1
            answer = note + chat_markdown(result["markdown"])
        elif kind == "map":
            payload["meetingId"] = scope.get("id") if scope.get("kind") == "record" else None
            result = mindmap(core, payload, date_of, today)
            answer = tree_text(result["tree"])
    except ValueError as exc:
        return {"answer": str(exc), "sources": [], "mode": "материал", "confirmed": False}
    return {"answer": answer, "sources": [], "mode": "материал", "confirmed": True, "material": {"kind": kind, "result": result}}
