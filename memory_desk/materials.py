"""Материалы по запросу: протокол встречи, карта (mind map) и презентация .pptx.

Все материалы собираются из уже извлечённых фактов (краткое содержание, решения, задачи со статусами, риски,
вопросы), поэтому работают и без модели. Локальная модель, если она доступна, добавляет связный текст
(«Ход обсуждения» в протоколе, слайд «Главное» в презентации); при её недоступности материал остаётся полным."""
from __future__ import annotations

import math
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import attachments, bookmarks, config, digest, knowledge, storage

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


# --------------------------------------------------------------------------- презентация

ACCENT = (0x0A, 0x6C, 0xFF)
INK = (0x1D, 0x1D, 0x1F)
MUTED = (0x6E, 0x6E, 0x73)


def _presentations_dir() -> Path:
    folder = config.DATA_DIR / "presentations"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def deck_path(name: str) -> Path:
    if "/" in name or "\\" in name or name.startswith(".") or not name.endswith(".pptx"):
        raise ValueError("Недопустимое имя файла")
    path = _presentations_dir() / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def deck_url(name: str) -> str:
    return "/api/materials/deck/" + urllib.parse.quote(name)


def list_decks() -> list[dict]:
    folder = _presentations_dir()
    return [{"name": p.name, "size": p.stat().st_size, "created": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            for p in sorted(folder.glob("*.pptx"), key=lambda p: p.stat().st_mtime, reverse=True)]


def _highlights(sc: Scope, facts: list[digest.MeetingFacts], st: dict) -> tuple[list[str], str]:
    """Слайд «Главное»: текст модели или цифры. Возвращает (пункты, режим)."""
    numbers = [digest.stats_line(st) + "."]
    latest, seen = [], set()
    for f in reversed(facts):
        if f.summary and f.summary[0] not in seen:
            seen.add(f.summary[0])
            latest.append(f"{digest.short(f.date)}: {f.summary[0]}")
    latest = latest[:3]
    if sc.intent is None:
        return numbers + latest, "structured"
    parts = digest.chunks(facts, sc.intent.project is None)
    body = "\n".join(parts)
    if len(body) > digest.BUDGET:  # длинный период: для слайда достаточно свежих записей
        body = body[-digest.BUDGET:]
    prompt = ("Ты помощник по архиву рабочих встреч. Напиши 4–6 коротких пунктов для слайда «Главное»: что сделано и решено, "
              "что в работе, что под угрозой. Только по фактам ниже, ничего не выдумывай, по одному предложению на пункт, "
              "каждый пункт с новой строки со знаком «-».\n\n" + digest.RULES + "\n\nФакты:\n" + body + "\n\nГЛАВНОЕ:\n")
    try:
        text = digest.generate(prompt, 450)
    except OSError:
        return numbers + latest, "structured"
    points = [re.sub(r"^[\-•*\d.\s]+", "", line).replace("**", "").strip() for line in text.splitlines()]
    points = [p for p in points if p][:6]
    return (numbers + points, "model") if points else (numbers + latest, "structured")


class Slides:
    """Оформление презентаций Memory Desk (16:9, синяя полоса, номер слайда): общее для презентации по области и дайджеста."""

    def __init__(self, today: date) -> None:
        from pptx import Presentation  # noqa: PLC0415 — тяжёлый импорт нужен только здесь
        from pptx.util import Inches  # noqa: PLC0415
        self.today = today
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(13.333), Inches(7.5)
        self.blank = self.prs.slide_layouts[6]
        self.n = 0

    def textbox(self, slide, left, top, width, height, text, size, bold=False, color=INK):
        from pptx.dml.color import RGBColor  # noqa: PLC0415
        from pptx.util import Inches, Pt  # noqa: PLC0415
        box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
        frame = box.text_frame
        frame.word_wrap = True
        paragraph = frame.paragraphs[0]
        paragraph.text = text
        run = paragraph.runs[0]
        run.font.size, run.font.bold, run.font.color.rgb = Pt(size), bold, RGBColor(*color)
        return frame

    def new_slide(self, title: str, subtitle: str = ""):
        from pptx.dml.color import RGBColor  # noqa: PLC0415
        from pptx.util import Inches  # noqa: PLC0415
        self.n += 1
        slide = self.prs.slides.add_slide(self.blank)
        bar = slide.shapes.add_shape(1, 0, 0, self.prs.slide_width, Inches(0.18))
        bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(*ACCENT); bar.line.fill.background()
        self.textbox(slide, 0.7, 0.5, 11.9, 0.9, title, 32, True)
        if subtitle:
            self.textbox(slide, 0.7, 1.25, 11.9, 0.5, subtitle, 16, False, MUTED)
        self.textbox(slide, 0.7, 6.95, 9, 0.4, f"Memory Desk · {_ru_date(self.today.isoformat())}", 11, False, MUTED)
        self.textbox(slide, 11.9, 6.95, 0.8, 0.4, str(self.n), 11, False, MUTED)
        return slide

    def cover(self, head: str, sub: str, stats: str) -> None:
        from pptx.dml.color import RGBColor  # noqa: PLC0415
        from pptx.util import Inches  # noqa: PLC0415
        self.n += 1
        slide = self.prs.slides.add_slide(self.blank)
        band = slide.shapes.add_shape(1, 0, 0, self.prs.slide_width, Inches(7.5))
        band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*ACCENT); band.line.fill.background()
        white = (255, 255, 255)
        self.textbox(slide, 0.9, 1.5, 11.5, 2.6, _clip(head, 110), 40 if len(head) <= 45 else 30, True, white)
        self.textbox(slide, 0.9, 4.3, 11.5, 0.6, sub, 22, False, white)
        self.textbox(slide, 0.9, 5.1, 11.5, 1.0, stats, 18, False, white)
        self.textbox(slide, 0.9, 6.6, 11.5, 0.5, f"Подготовлено в Memory Desk · {_ru_date(self.today.isoformat())}", 14, False, white)

    def bullets(self, title: str, items: list[str], per: int = 6, subtitle: str = "") -> None:
        pages = [items[i:i + per] for i in range(0, len(items), per)] or [[]]
        for index, page in enumerate(pages, 1):
            slide = self.new_slide(title + (f" ({index}/{len(pages)})" if len(pages) > 1 else ""), subtitle)
            top = 1.9
            for text in page:
                self.textbox(slide, 0.9, top, 11.6, 0.8, "•  " + _clip(text, 150), 20)
                top += 0.82

    def table(self, title: str, headers: tuple[str, ...], rows: list[tuple[str, ...]], widths: tuple[float, ...], per: int = 7) -> None:
        from pptx.util import Inches, Pt  # noqa: PLC0415
        pages = max(1, (len(rows) + per - 1) // per)
        for index in range(0, max(len(rows), 1), per):
            page = rows[index:index + per]
            slide = self.new_slide(title + (f" ({index // per + 1}/{pages})" if pages > 1 else ""))
            table = slide.shapes.add_table(len(page) + 1, len(headers), Inches(0.7), Inches(1.7), Inches(11.9), Inches(0.5 * (len(page) + 1))).table
            for col, width in enumerate(widths):
                table.columns[col].width = Inches(width)
            for col, head in enumerate(headers):
                cell = table.cell(0, col)
                cell.text = head
                cell.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
            for row, values in enumerate(page, 1):
                for col, value in enumerate(values):
                    cell = table.cell(row, col)
                    cell.text = value
                    cell.text_frame.paragraphs[0].runs[0].font.size = Pt(14)

    def save(self, path: Path) -> int:
        self.prs.save(path)
        return len(self.prs.slides)


def build_deck(core, scope: dict, date_of: Callable, today: date | None = None) -> dict:
    today = today or date.today()
    sc = resolve(core, scope, date_of, today)
    if not sc.meetings:
        raise ValueError("За выбранный период записей нет")
    facts = digest.gather(sc.meetings, date_of, today)
    st = digest.stats(facts)
    highlights, mode = _highlights(sc, facts, st)
    multi = not sc.single and (sc.intent is None or sc.intent.project is None)
    deck = Slides(today)

    if sc.intent is not None:
        head = " · ".join(x for x in (sc.intent.project["title"] if sc.intent.project else "", sc.intent.person["name"] if sc.intent.person else "") if x) or "Все проекты"
        sub = f"{sc.intent.label} ({digest.short(sc.intent.since.isoformat())} – {digest.short(sc.intent.until.isoformat())})"
    else:
        head, sub = sc.title, _ru_date(sc.label)
    deck.cover(head, sub, digest.stats_line(st) + ".")
    deck.bullets("Главное", highlights, 7)

    where = (lambda f: f" ({digest.short(f.date)}{', ' + f.project if multi and f.project else ''})")
    decisions = [d["text"] + (" — не подтверждено" if d["draft"] else "") + where(f) for f in facts for d in f.decisions if not d.get("replaced")]
    if decisions:
        deck.bullets("Решения", decisions)

    open_rows = []
    for f in facts:
        for t in f.open:
            status = "просрочено" if t["overdue"] else ("черновик" if t["draft"] else "в работе")
            open_rows.append((_clip(t["text"], 90), t["owner"] or "не указан", _due(t), status))
    open_rows.sort(key=lambda r: (r[3] != "просрочено", r[2]))
    if open_rows:
        deck.table("Задачи в работе", ("Задача", "Ответственный", "Срок", "Статус"), open_rows, (6.2, 2.4, 1.7, 1.6))

    done = [t["text"] + (f" — {t['owner']}" if t["owner"] else "") + where(f) for f in facts for t in f.done]
    if done:
        deck.bullets("Выполнено", done)

    risks = [r + where(f) for f in facts for r in f.risks]
    questions = [q + where(f) for f in facts for q in f.questions]
    if risks:
        deck.bullets("Риски", risks)
    if questions:
        deck.bullets("Открытые вопросы", questions)

    timeline = [f"{digest.short(f.date)} — {f.title}" + (f" ({f.project})" if multi and f.project else "") for f in facts]
    deck.bullets("Хронология встреч", timeline, 8, f"Всего записей: {len(facts)}")

    name = safe_name(f"{sc.title} — {today.isoformat()}", 90) + ".pptx"
    slides = deck.save(_presentations_dir() / name)
    return {"name": name, "title": sc.title, "period": sc.label, "slides": slides, "records": len(facts), "mode": mode, "stats": st,
            "url": deck_url(name)}


# --------------------------------------------------------------------------- запрос из чата

VERBS = re.compile(r"сдела|созда|постро|нарису|подготов|состав|сформир|сгенерир|собери|нужн|хочу|выгрузи|оформи|оформл|дай\b|пришли", re.IGNORECASE)
KINDS = (("deck", re.compile(r"презентац|слайд|powerpoint|pptx|keynote", re.IGNORECASE)),
         ("memo", re.compile(r"служебн\w*\s+записк|\bзаписк[ауие]\s+(?:руковод|начальник|директор)", re.IGNORECASE)),
         ("brief", re.compile(r"\bбриф", re.IGNORECASE)),
         ("followup", re.compile(r"follow[- ]?up|фолл?оу[- ]?ап|письм\w*\s+(?:участник|с\s+итог|по\s+итог)|итогов\w+\s+письм", re.IGNORECASE)),
         ("protocol", re.compile(r"протокол", re.IGNORECASE)),
         ("map", re.compile(r"\bкарт[ауыеой]\w*|mind ?map|майнд|интеллект[- ]карт", re.IGNORECASE)))


def request_kind(question: str) -> str | None:
    """Просит ли вопрос создать материал («сделай презентацию по проекту…», «составь протокол»)."""
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
    с кнопкой «Открыть окно» (правка, копирование, Obsidian), презентация — файлом."""
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
            if re.search(r"презентац|слайд|pptx|keynote|powerpoint", question, re.IGNORECASE):
                result = projectdigest.build_deck(core, key, date_of, today, project_id)
                kind = "deck"
                answer = (f"Презентация-дайджест готова: {result['slides']} слайдов, «{result['period']}».\n\n"
                          f"{'Вводный абзац написала локальная модель.' if result['mode'] == 'model' else 'Вводный абзац собран из цифр без модели.'}")
            else:
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
        else:
            result = build_deck(core, payload, date_of, today)
            answer = (f"Презентация готова: {result['slides']} слайдов по записям за период «{result['period']}».\n\n"
                      f"{'Слайд «Главное» написала локальная модель.' if result['mode'] == 'model' else 'Слайд «Главное» собран без модели.'}")
    except ValueError as exc:
        return {"answer": str(exc), "sources": [], "mode": "материал", "confirmed": False}
    return {"answer": answer, "sources": [], "mode": "материал", "confirmed": True, "material": {"kind": kind, "result": result}}
