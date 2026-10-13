"""Артефакты по записям: служебная записка, бриф и черновик follow-up письма участникам.

Каждый пункт берётся из уже извлечённых решений, задач, рисков, вопросов и краткого содержания, а не придумывается
моделью. У каждого пункта обязательно есть таймкод `⏱ 00:12:30` — место в записи, где это прозвучало:
 • если в заметке указан `evidence_time`, он проверяется (не позже конца записи и стенограммы);
 • если не указан или не проходит проверку, место ищется в стенограмме по совпадению слов;
 • пункт, для которого место не нашлось, в документ не попадает — он перечислен в `skipped` и в сноске.
Локальная модель, если доступна, пишет только вводную фразу (без новых фактов); без неё документ собирается так же полно.
Письмо — только черновик: отправка не выполняется."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from . import bookmarks, digest, knowledge, materials, storage

KINDS = {
    "memo": {"title": "Служебная записка", "folder": "Служебные записки", "suffix": "записка"},
    "brief": {"title": "Бриф", "folder": "Брифы", "suffix": "бриф"},
    "followup": {"title": "Письмо участникам", "folder": "Письма", "suffix": "письмо"},
}
STAMP = re.compile(r"⏱\s*(\d{2}:\d{2}:\d{2})")
MAX_SECTION = 10
TOLERANCE = 5          # секунд запаса к длительности записи
MIN_SHARE = 0.4        # доля слов пункта, найденных в реплике, чтобы считать её местом в записи
PLACEHOLDER_TO = "[укажите адресата]"
PLACEHOLDER_FROM = "[ваше имя]"


# --------------------------------------------------------------------------- пункты с таймкодами

@dataclass
class Item:
    kind: str                       # summary | decision | task | risk | question
    text: str
    meeting: object
    date: str
    t: str = ""                     # ЧЧ:ММ:СС
    sec: int = 0
    found: bool = False             # таймкод найден по стенограмме, а не взят из заметки
    owner: str = ""
    due: str = ""
    due_text: str = ""
    overdue: bool = False
    draft: bool = False
    replaced: bool = False
    updated: str = ""              # дата последней правки задачи (для «закрыто за период»)


@dataclass
class Gathered:
    items: list[Item] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def of(self, kind: str) -> list[Item]:
        return [i for i in self.items if i.kind == kind]


def _stems(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"\w+", text.casefold().replace("ё", "е")) if len(w) > 3}


class _Recording:
    """Реплики и границы одной записи: по ним проверяются и ищутся таймкоды. Стенограмма читается только когда она нужна."""

    def __init__(self, meeting) -> None:
        self.meeting = meeting
        self._segments: list[dict] | None = None
        self.duration = knowledge.to_seconds(meeting.duration) if meeting.duration else None

    @property
    def segments(self) -> list[dict]:
        if self._segments is None:
            self._segments = [s for s in knowledge.transcript_segments(self.meeting) if s.get("t")]
            for s in self._segments:
                s["stems"] = _stems(s["text"])
        return self._segments

    @property
    def bound(self) -> int | None:
        last = max((s["sec"] for s in self.segments), default=None)
        if self.duration is not None:
            return max(self.duration, last or 0)
        return last + 120 if last is not None else None

    def valid(self, stamp: str) -> int | None:
        """Секунды, если таймкод оформлен как ЧЧ:ММ:СС и попадает внутрь записи."""
        if not re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", stamp or ""):
            return None
        sec = knowledge.to_seconds(stamp)
        if sec is None:
            return None
        if self.duration is not None and sec <= self.duration + TOLERANCE:
            return sec     # внутри длительности записи: стенограмма не нужна
        bound = self.bound
        return None if bound is not None and sec > bound + TOLERANCE else sec

    def locate(self, text: str) -> int | None:
        """Место в записи по совпадению слов: лучшая реплика или пара соседних."""
        want = _stems(text)
        if not want or not self.segments:
            return None
        need = 1 if len(want) <= 2 else 2
        best: tuple[float, int] | None = None

        def offer(score: float, sec: int) -> None:
            nonlocal best
            if best is None or score > best[0]:
                best = (score, sec)

        for index, seg in enumerate(self.segments):
            hit = len(want & seg["stems"])
            if hit >= need and hit / len(want) >= MIN_SHARE:
                offer(hit / len(want), seg["sec"])
            if index + 1 < len(self.segments):   # мысль могла перейти в следующую реплику; одиночная реплика предпочтительнее
                nxt = self.segments[index + 1]
                joint = len(want & (seg["stems"] | nxt["stems"]))
                if joint >= need and joint / len(want) >= MIN_SHARE:
                    first = len(want & seg["stems"]) >= len(want & nxt["stems"])
                    offer(joint / len(want) - 0.05, seg["sec"] if first else nxt["sec"])
        return best[1] if best else None


def gather(meetings: list, date_of: Callable, today: date | None = None, *, kinds: set[str] | None = None,
           task_status: tuple[str, ...] = ("draft", "open")) -> Gathered:
    """Решения, задачи, риски, вопросы и резюме записей; у каждого пункта проверенный таймкод.
    `kinds` ограничивает набор видов пунктов; `task_status` — какие задачи брать (по умолчанию в работе и черновики;
    ("done",) даёт выполненные, у них `kind == "done"`)."""
    want = lambda kind: kinds is None or kind in kinds  # noqa: E731
    today_iso = (today or date.today()).isoformat()
    out = Gathered()
    ids = [m.id for m in meetings]
    decisions: dict[str, list] = {}
    tasks: dict[str, list] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        if want("decision"):
            for r in storage.rows(f"SELECT * FROM decisions WHERE meeting_id IN ({marks}) AND status!='rejected' ORDER BY rowid", tuple(ids)):
                decisions.setdefault(r["meeting_id"], []).append(r)
        if want("task") or want("done"):
            states = ",".join("?" * len(task_status))
            for r in storage.rows(f"SELECT * FROM tasks WHERE meeting_id IN ({marks}) AND status IN ({states}) ORDER BY rowid", tuple(ids) + tuple(task_status)):
                tasks.setdefault(r["meeting_id"], []).append(r)
    for meeting in meetings:
        recording = _Recording(meeting)
        day = date_of(meeting) or ""

        def add(kind: str, text: str, stamp: str, **extra) -> None:
            text = digest._clean(text, 260)
            if not text:
                return
            sec = recording.valid(stamp)
            found = False
            if sec is None:
                sec = recording.locate(text)
                found = sec is not None
            if sec is None:
                out.skipped.append({"kind": kind, "text": text, "meetingId": meeting.id, "meetingTitle": meeting.title,
                                    "reason": "нет места в записи" if not stamp else "таймкод вне записи, место в стенограмме не найдено"})
                return
            out.items.append(Item(kind, text, meeting, day, bookmarks.stamp(sec), sec, found, **extra))

        for r in decisions.get(meeting.id, []):
            if r.get("replaced_by"):
                continue  # заменённое решение уже не действует
            add("decision", r["text"], r["evidence_time"] or "", draft=r["status"] == "draft")
        for r in tasks.get(meeting.id, []):
            finished = r["status"] == "done"
            if not want("done" if finished else "task"):
                continue
            owner = (r["owner"] or "").strip()
            owner = "" if owner.casefold() in {"не указано", "не указан"} else owner
            deadline = (r["deadline"] or "").strip()
            iso = deadline if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline) else ""
            add("done" if finished else "task", r["text"], r["evidence_time"] or "", owner=owner, due=iso,
                due_text="" if iso or deadline.casefold() in {"", "не указан", "не указано"} else deadline,
                overdue=bool(iso) and iso < today_iso and not finished, draft=r["status"] == "draft",
                updated=str(r.get("updated_at") or "")[:10])
        if want("summary"):
            for line in knowledge.summary_bullets(meeting, 6):
                fact = storage.parse_fact(line)
                add("summary", fact["text"], fact["evidence_time"])
        if want("risk"):
            for fact in knowledge.fact_items(meeting, "Риски")[:4]:
                add("risk", fact["text"], fact["evidence_time"])
        if want("question"):
            for fact in knowledge.fact_items(meeting, "Открытые вопросы")[:4]:
                add("question", fact["text"], fact["evidence_time"])
    return out


# --------------------------------------------------------------------------- оформление строк

def _stamp(item: Item) -> str:
    return f"`⏱ {item.t}`"


def _label(title: str) -> str:
    """Название записи в тексте документа: без символов, ломающих ссылку Obsidian."""
    return title.replace("|", "/").replace("[", "(").replace("]", ")")


def _where(item: Item, multi: bool, links: bool) -> str:
    """Откуда пункт: для нескольких записей — ссылка (Obsidian) или название, дата и таймкод."""
    if not multi:
        return _stamp(item)
    title = _label(item.meeting.title)
    if links:
        rel = knowledge.note_relative_path(item.meeting.path).removesuffix(".md")
        label = f"[[{rel}|{title}]]"
    else:
        label = f"«{title}»"
    return f"{label}, {digest.short(item.date)} · {_stamp(item)}"


def _cell(value: str) -> str:
    return str(value).replace("|", "/").replace("\n", " ")


def _due(item: Item) -> str:
    return digest.short(item.due) if item.due else (item.due_text or "не указан")


def _owner(item: Item, me: str) -> str:
    return me if item.owner.casefold() == "вы" else item.owner


def _emit(lines: list[str], rows: list[str], empty: str, limit: int = MAX_SECTION) -> None:
    """Строки раздела (не больше `limit`) или фраза, если раздел пуст."""
    if not rows:
        lines.append(empty)
        return
    lines.extend(rows[:limit])
    if len(rows) > limit:
        lines.append(f"_Показаны первые {limit} из {len(rows)}, остальные — в записях._")


def _decision(item: Item, multi: bool, links: bool, number: int | None = None) -> str:
    mark = " _(не подтверждено)_" if item.draft else ""
    lead = f"{number}. " if number else "- "
    return f"{lead}{item.text}{mark} — {_where(item, multi, links)}"


def _task(item: Item, multi: bool, links: bool, me: str, with_owner: bool = True, with_due: bool = True) -> str:
    bits = []
    if with_owner:
        bits.append(f"ответственный: {_owner(item, me) or 'не назначен'}")
    if with_due and (item.due or item.due_text):
        bits.append(f"срок: {_due(item)}" + (" — просрочено" if item.overdue else ""))
    if item.draft:
        bits.append("не подтверждено")
    tail = f" ({'; '.join(bits)})" if bits else ""
    return f"- {item.text}{tail} — {_where(item, multi, links)}"


def _plain(item: Item, multi: bool, links: bool) -> str:
    return f"- {item.text} — {_where(item, multi, links)}"


# --------------------------------------------------------------------------- вводная фраза

def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def _lead(prompt_goal: str, items: list[Item], fallback: str) -> tuple[str, str]:
    """Вводная фраза: пишет модель (только перефразировать пункты), иначе — структурная. Возвращает (текст, режим).
    Ответ модели с цифрами, которых нет в пунктах, отбрасывается: новых фактов во вводной быть не должно."""
    source = [i.text for i in items[:12]]
    if not source:
        return fallback, "structured"
    prompt = (f"Ты помощник по деловым документам. {prompt_goal} Пиши только по пунктам ниже, ничего не добавляй: ни цифр, "
              "ни имён, ни дат, которых в них нет. Без списков и вступлений, 1–2 предложения, по-русски.\n\nПункты:\n"
              + "\n".join("- " + s for s in source) + "\n\nТЕКСТ:\n")
    try:
        answer = digest.generate(prompt, 160).strip()
    except OSError:
        return fallback, "structured"
    if not answer or len(answer) > 600 or "\n" in answer or not _numbers(answer) <= _numbers(" ".join(source)):
        return fallback, "structured"
    return answer, "model"


def _count(n: int, one: str, few: str, many: str) -> str:
    return digest._plural(n, one, few, many)


def _summary_line(g: Gathered) -> str:
    parts = [(_count(len(g.of("decision")), "решение", "решения", "решений")),
             _count(len(g.of("task")), "поручение", "поручения", "поручений"),
             _count(len(g.of("question")), "открытый вопрос", "открытых вопроса", "открытых вопросов")]
    return ", ".join(parts)


def _what(sc: materials.Scope) -> str:
    if sc.single:
        meeting = sc.meetings[0]
        return f"встречи «{meeting.title}» от {materials._ru_date(sc.label)}" if sc.label else f"встречи «{meeting.title}»"
    return f"записей ({len(sc.meetings)}): {sc.title}"


# --------------------------------------------------------------------------- три документа

def _memo(sc, g, ctx) -> tuple[list[str], str]:
    multi, me, links = not sc.single, "я", True
    lines = [f"# Служебная записка: {sc.title}", "",
             f"**Кому:** {ctx['addressee'] or PLACEHOLDER_TO}", f"**От кого:** {PLACEHOLDER_FROM}",
             f"**Дата:** {materials._ru_date(ctx['today'].isoformat())}", f"**Тема:** {sc.title}"]
    summary = g.of("summary")
    fallback = f"По итогам {_what(sc)} зафиксировано: {_summary_line(g)}."
    lead, mode = _lead("Напиши вводную часть служебной записки: суть вопроса.", summary + g.of("decision"), fallback)
    lines += ["", "## Суть", "", lead, ""]
    _emit(lines, [_plain(i, multi, links) for i in summary[:3]], "Краткое содержание в записях не заполнено.")
    lines += ["", "## Обоснование", ""]
    _emit(lines, [_plain(i, multi, links) for i in summary[3:] + g.of("risk")], "Отдельных оснований в записях не зафиксировано.")
    lines += ["", "## Решения", ""]
    _emit(lines, [_decision(i, multi, links, n) for n, i in enumerate(g.of("decision"), 1)], "Решений не зафиксировано.")
    lines += ["", "## Что просим", ""]
    asks = [_task(i, multi, links, me) for i in g.of("task")]
    asks += [f"- Дать ответ: {i.text} — {_where(i, multi, links)}" for i in g.of("question")]
    _emit(lines, asks, "Просьб и открытых вопросов нет.")
    return lines, mode


def _brief(sc, g, ctx) -> tuple[list[str], str]:
    multi, me, links = not sc.single, "Вы", True
    lines = [f"# Бриф: {sc.title}", "", f"**Период:** {sc.label}" if not sc.single else f"**Дата:** {materials._ru_date(sc.label)}",
             f"**Записей:** {len(sc.meetings)}"]
    goal = ""
    project = ctx.get("project")
    if project:
        stored = storage.one("SELECT goal,description FROM projects WHERE id=?", (project["id"],)) or {}
        goal = " ".join(str(project.get("goal") or stored.get("goal") or stored.get("description") or "").split())[:600]
    mode = "structured"
    if not goal:
        fallback = f"[впишите цель]. По записям: {_summary_line(g)}."
        goal, mode = _lead("Сформулируй цель работы одним–двумя предложениями.", g.of("summary") + g.of("decision"), fallback)
    lines += ["", "## Цель", "", goal, "", "## Контекст", ""]
    _emit(lines, [_plain(i, multi, links) for i in g.of("summary")], "Краткое содержание в записях не заполнено.")
    lines += ["", "## Договорённости", ""]
    _emit(lines, [_decision(i, multi, links) for i in g.of("decision")], "Договорённостей не зафиксировано.")
    tasks = g.of("task")
    dated = sorted([t for t in tasks if t.due or t.due_text], key=lambda t: (t.due or "9999", t.due_text))
    lines += ["", "## Сроки", ""]
    if dated:
        lines += ["| Срок | Что сделать | Ответственный | В записи |", "| --- | --- | --- | --- |"]
        for t in dated[:MAX_SECTION * 2]:
            lines.append(f"| {_cell(_due(t))}{' ⚠' if t.overdue else ''} | {_cell(t.text)} | {_cell(_owner(t, me) or 'не назначен')} | {_cell(_where(t, multi, False))} |")
    else:
        lines.append("Сроки в записях не названы.")
    lines += ["", "## Ответственные", ""]
    by_owner: dict[str, list[Item]] = {}
    for t in tasks:
        by_owner.setdefault(_owner(t, me) or "Не назначен", []).append(t)
    if by_owner:
        for owner in sorted(by_owner, key=lambda o: (o == "Не назначен", o.casefold())):
            lines.append(f"- **{owner}** — {_count(len(by_owner[owner]), 'задача', 'задачи', 'задач')}")
            lines += [f"  - {t.text} — {_where(t, multi, links)}" for t in by_owner[owner][:MAX_SECTION]]
    else:
        lines.append("Открытых задач нет.")
    lines += ["", "## Открытые вопросы", ""]
    _emit(lines, [_plain(i, multi, links) for i in g.of("question")], "Открытых вопросов нет.")
    risks = g.of("risk")
    if risks:
        lines += ["", "## Риски", ""]
        _emit(lines, [_plain(i, multi, links) for i in risks], "")
    return lines, mode


def _recipients(sc: materials.Scope) -> list[str]:
    people = storage.people_by_meeting()
    names: dict[str, str] = {}
    for meeting in sc.meetings:
        for p in people.get(meeting.id, []):
            names.setdefault(p["id"], p["name"])
    return sorted(names.values(), key=str.casefold)


def _followup(sc, g, ctx) -> tuple[list[str], str]:
    multi, me, links = not sc.single, "Я", False
    subject = f"Итоги {_what(sc)}" if sc.single else f"Итоги: {sc.title}"
    names = _recipients(sc)
    lines = [f"# Письмо: {subject}", "", "> Черновик. Письмо не отправлено: проверьте текст и отправьте сами.", "",
             f"**Тема:** {subject}", f"**Кому:** {', '.join(names) if names else PLACEHOLDER_TO}", "", "Коллеги, добрый день!", ""]
    fallback = (f"Направляю краткие итоги {_what(sc)}: {_summary_line(g)}.")
    lead, mode = _lead("Напиши две фразы вводной части письма участникам встречи: кратко, чем закончилось обсуждение.",
                       g.of("decision") + g.of("summary"), fallback)
    lines += [lead, "", "## Что решили", ""]
    _emit(lines, [_decision(i, multi, links, n) for n, i in enumerate(g.of("decision"), 1)], "Решений не зафиксировано.")
    lines += ["", "## Кто что делает", ""]
    by_owner: dict[str, list[Item]] = {}
    for t in g.of("task"):
        by_owner.setdefault(_owner(t, me) or "Ответственный не назначен", []).append(t)
    if by_owner:
        for owner in sorted(by_owner, key=lambda o: (o == "Ответственный не назначен", o.casefold())):
            lines += [f"**{owner}**", ""]
            lines += [_task(t, multi, links, me, with_owner=False) for t in by_owner[owner][:MAX_SECTION]]
            lines.append("")
        lines.pop()
    else:
        lines.append("Поручений нет.")
    questions = g.of("question")
    if questions:
        lines += ["", "## Что осталось открытым", ""]
        _emit(lines, [_plain(i, multi, links) for i in questions], "")
    lines += ["", "Если что-то записано неточно, напишите — поправим. Таймкод рядом с пунктом показывает место в записи.", "", PLACEHOLDER_FROM]
    return lines, mode


BUILDERS = {"memo": _memo, "brief": _brief, "followup": _followup}


# --------------------------------------------------------------------------- вход

def untimed(markdown: str) -> list[str]:
    """Пункты списков и строки таблиц без таймкода. В готовом документе их нет (используется проверками и тестами).
    Исключение — сводная таблица дайджеста (`| Проект | …`): в ней только числа, а не факты из записей."""
    bad = []
    counts_table = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            counts_table = False
            continue
        if line.startswith("| Проект |"):
            counts_table = True
        if counts_table or STAMP.search(line):
            continue
        if re.match(r"(?:[-*]|\d+\.)\s+\S", line):
            if re.fullmatch(r"- \*\*[^*]+\*\* — \d+ \w+", line):
                continue  # заголовок человека в брифе («Иванов — 2 задачи»): его задачи с таймкодами идут ниже
            bad.append(line)
        elif line.startswith("|") and not re.fullmatch(r"\|?[\s:\-|]+\|?", line) and not line.startswith("| Срок |"):
            bad.append(line)
    return bad


def build(core, kind: str, scope: dict, date_of: Callable, today: date | None = None, addressee: str = "") -> dict:
    if kind not in KINDS:
        raise ValueError("Неизвестный вид документа")
    today = today or date.today()
    sc = materials.resolve(core, scope, date_of, today)
    if not sc.meetings:
        raise ValueError("За выбранный период записей нет")
    g = gather(sc.meetings, date_of, today)
    if not g.items:
        raise ValueError("В записях нет решений, задач, вопросов и резюме с привязкой к месту в записи: документ собирать не из чего")
    project = next((p for p in core.projects() if p["id"] == scope.get("projectId")), None) if scope.get("projectId") else None
    ctx = {"today": today, "addressee": " ".join(str(addressee or "").split())[:200], "project": project}
    lines, mode = BUILDERS[kind](sc, g, ctx)
    skipped = g.skipped
    foot = [""]
    if skipped:
        foot.append(f"_Не включено пунктов без привязки к месту в записи: {len(skipped)}._")
        foot.append("")
    foot.append(f"_Составлено автоматически из записей ({len(sc.meetings)}) Memory Desk; проверьте по таймкодам перед использованием._")
    markdown = "\n".join(lines + foot) + "\n"
    meetings = sc.meetings
    stamp_day = sc.label if sc.single else today.isoformat()
    meta = KINDS[kind]
    return {
        "kind": kind, "title": f"{meta['title']}: {sc.title}", "markdown": markdown, "mode": mode,
        "records": len(meetings), "date": stamp_day,
        "fileName": materials.safe_name(f"{stamp_day} — {sc.title} — {meta['suffix']}") + ".md",
        "refs": [{"id": m.id, "title": m.title, "label": _label(m.title)} for m in meetings],
        "count": len(g.items), "located": sum(1 for i in g.items if i.found),
        "skipped": [{k: v for k, v in s.items()} for s in skipped],
    }
