"""Сводки для чата: «что сделано по проекту КХД за неделю / месяц».

Обычный ответ чата строится по 3–4 найденным фрагментам. Для обзорных вопросов этого мало, поэтому здесь
берутся ВСЕ записи подходящего проекта/человека за период, из них собираются уже извлечённые факты
(краткое содержание, решения, задачи со статусами, риски, вопросы), и локальная модель пишет по ним связный
итог. Длинные периоды делятся на части и сводятся в два шага. Без модели выдаётся структурная сводка."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from . import config, knowledge, storage

MONTH_STEMS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
               "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
MONTH_NAMES = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
NUMBER_WORDS = {"два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "десять": 10}
SUMMARY_WORDS = re.compile(
    r"что сделан|что сделал|сделано|что делал|итог|сводк|саммари|резюме|обзор|что было|что происходило|чем занима|"
    r"что нового|прогресс|как продвига|что изменилось|статус|что по проекту|расскажи про проект|"
    r"синтез|свод|выжимк|сжат|общая картина|в целом|главное|анализ проекта|картина по|что там по|что у нас по", re.IGNORECASE)
QUESTION_WORDS = {"кто", "кого", "кому", "когда", "почему", "зачем", "сколько", "где", "куда", "какой", "какая", "какие", "каком",
                  "ли", "можно", "нужно", "есть", "был", "была", "было", "были", "будет", "как"}
BUDGET = 8500          # знаков фактов в одном запросе к модели
MAX_SOURCES = 10
RU_SHORT = ["", "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def short(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {RU_SHORT[d.month]}"


# --------------------------------------------------------------------------- период и фильтры

@dataclass
class Intent:
    since: date
    until: date
    label: str
    explicit_period: bool
    project: dict | None = None
    person: dict | None = None


def parse_period(question: str, today: date) -> tuple[date, date, str] | None:
    q = question.casefold().replace("ё", "е")
    monday = today - timedelta(days=today.weekday())
    if re.search(r"\bсегодня\b", q):
        return today, today, "сегодня"
    if re.search(r"\bвчера\b", q):
        d = today - timedelta(days=1)
        return d, d, "вчера"
    if re.search(r"(на|за) прошл\w+ недел", q):
        return monday - timedelta(days=7), monday - timedelta(days=1), "прошлая неделя"
    if re.search(r"(на|за|в течение) (этой|эту|текущ\w+) недел", q):
        return monday, today, "эта неделя"
    if re.search(r"(в|за) прошл\w+ месяц", q):
        last_prev = today.replace(day=1) - timedelta(days=1)
        return last_prev.replace(day=1), last_prev, "прошлый месяц"
    if re.search(r"(в|за) (этом|этот|текущ\w+) месяц", q):
        return today.replace(day=1), today, "этот месяц"
    if re.search(r"за (последн\w+ |прошедш\w+ )?квартал", q):
        return today - timedelta(days=90), today, "последние 90 дней"
    if re.search(r"за (последн\w+ |прошедш\w+ )?год\b", q):
        return today - timedelta(days=365), today, "последний год"
    numbered = re.search(r"за (?:последн\w+ |прошедш\w+ )?(\d{1,3}|" + "|".join(NUMBER_WORDS) + r") (дн\w+|недел\w+|месяц\w*)", q)
    if numbered:
        n = int(numbered.group(1)) if numbered.group(1).isdigit() else NUMBER_WORDS[numbered.group(1)]
        unit = numbered.group(2)
        days = n * (7 if unit.startswith("недел") else 30 if unit.startswith("месяц") else 1)
        if 0 < days <= 3650:
            return today - timedelta(days=days), today, f"последние {days} дн."
    single = re.search(r"за (?:последн\w+ |прошедш\w+ )?(дн\w+|недел\w+|месяц\w*)", q)
    if single:
        unit = single.group(1)
        if unit.startswith("недел"):
            return today - timedelta(days=7), today, "последняя неделя"
        if unit.startswith("месяц"):
            return today - timedelta(days=30), today, "последние 30 дней"
        return today - timedelta(days=1), today, "последний день"
    month = re.search(r"\bв (январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре|октябре|ноябре|декабре)\b", q)
    if month:
        word = month.group(1)
        stem = "ма" if word == "мае" else word[:-1]
        number = next((v for k, v in MONTH_STEMS.items() if stem.startswith(k)), None)
        if number:
            year = today.year if number <= today.month else today.year - 1
            first = date(year, number, 1)
            last = date(year + (number == 12), number % 12 + 1, 1) - timedelta(days=1)
            return first, min(last, today), f"{MONTH_NAMES[number]} {year}"
    return None


def _stem_hit(word: str, text: str) -> bool:
    cut = max(3, len(word) - 2)
    return bool(re.search(rf"(?<!\w){re.escape(word[:cut])}", text))


def match_project(question: str, projects: list[dict]) -> dict | None:
    q = question.casefold()
    words = set(re.findall(r"\w+", q))
    best = None
    for p in projects:
        title = p["title"].casefold()
        if title in q or p["id"].casefold() in words:
            if best is None or len(title) > len(best["title"]):
                best = p
    return best


def match_person(question: str, people: list[dict]) -> dict | None:
    q = question.casefold().replace("ё", "е")
    hits = []
    for person in people:
        tokens = [t for t in re.findall(r"\w+", person["name"].casefold().replace("ё", "е")) if len(t) >= 3]
        if tokens and any(_stem_hit(t, q) for t in tokens[:2]):
            hits.append(person)
    return hits[0] if len(hits) == 1 else None


def subject(core, question: str, scope: dict) -> tuple[dict | None, dict | None]:
    """Проект и человек, о которых вопрос: из выбранной области чата или из текста вопроса."""
    kind = (scope or {}).get("kind", "all")
    projects = core.projects()
    project = person = None
    if kind == "project":
        key = scope.get("id") or scope.get("title") or ""
        project = next((p for p in projects if p["id"] == key or p["title"] == key or p["title"] == scope.get("title")), None)
    elif kind == "person":
        key = scope.get("id") or ""
        rows = storage.rows("SELECT id,name FROM people WHERE id=? OR name=?", (key, key))
        person = rows[0] if rows else None
    if project is None:
        project = match_project(question, projects)
    if person is None and kind != "project":
        person = match_person(question, storage.rows("SELECT id,name FROM people"))
    return project, person


def detect(core, question: str, scope: dict, today: date | None = None) -> Intent | None:
    """Обзорный ли это вопрос. Вопросы про одну запись и точечные вопросы остаются на обычном поиске."""
    scope = scope or {"kind": "all"}
    kind = scope.get("kind", "all")
    if kind == "record":
        return None
    today = today or date.today()
    period = parse_period(question, today)
    project, person = subject(core, question, scope)
    words = re.findall(r"\w+", question.casefold())
    # короткое «по проекту КХД» / «КХД» без вопросительных слов — тоже просьба обзора
    bare = (project is not None or person is not None) and len(words) <= 6 and not (QUESTION_WORDS & set(words)) \
        and "?" not in question
    if period is None and not SUMMARY_WORDS.search(question) and not bare:
        return None
    if kind == "days" and period is None:
        try:
            days = min(max(int(scope.get("days") or 7), 1), 3650)
        except (TypeError, ValueError):
            days = 7
        period = (today - timedelta(days=days), today, f"последние {days} дн.")
    explicit = period is not None
    if period is None:
        period = (today - timedelta(days=30), today, "последние 30 дней")
    # вопрос без периода и без проекта/человека («о чём были записи?») — обычный поиск
    if not explicit and project is None and person is None:
        return None
    return Intent(period[0], period[1], period[2], explicit, project, person)


# --------------------------------------------------------------------------- факты

@dataclass
class MeetingFacts:
    id: str
    title: str
    date: str
    project: str
    summary: list[str] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)   # text, draft
    done: list[dict] = field(default_factory=list)        # text, owner
    open: list[dict] = field(default_factory=list)        # text, owner, due, dueText, overdue, draft
    risks: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


def _clean(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def select_meetings(core, intent: Intent, date_of: Callable) -> list:
    people_ids: set[str] | None = None
    if intent.person:
        people_ids = {r["meeting_id"] for r in storage.rows("SELECT meeting_id FROM meeting_people WHERE person_id=?", (intent.person["id"],))}
    chosen = []
    since, until = intent.since.isoformat(), intent.until.isoformat()
    for m in core.live_meetings():
        d = date_of(m)
        if not d or d < since or d > until:
            continue
        if intent.project and not (m.project_id == intent.project["id"] or m.project == intent.project["title"]):
            continue
        if people_ids is not None and m.id not in people_ids:
            continue
        chosen.append(m)
    chosen.sort(key=lambda m: (date_of(m), m.time))
    return chosen


def gather(meetings: list, date_of: Callable, today: date | None = None) -> list[MeetingFacts]:
    today_iso = (today or date.today()).isoformat()
    ids = [m.id for m in meetings]
    decisions: dict[str, list] = {}
    tasks: dict[str, list] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in storage.rows(f"SELECT * FROM decisions WHERE meeting_id IN ({marks}) AND status!='rejected' ORDER BY rowid", tuple(ids)):
            decisions.setdefault(r["meeting_id"], []).append(r)
        for r in storage.rows(f"SELECT * FROM tasks WHERE meeting_id IN ({marks}) AND status!='rejected' ORDER BY rowid", tuple(ids)):
            tasks.setdefault(r["meeting_id"], []).append(r)
    out = []
    for m in meetings:
        f = MeetingFacts(m.id, m.title, date_of(m), m.project or "")
        f.summary = [_clean(s) for s in knowledge.summary_bullets(m, 3)]
        f.decisions = [{"text": _clean(r["text"]), "draft": r["status"] == "draft", "replaced": bool(r.get("replaced_by"))}
                       for r in decisions.get(m.id, [])]
        for r in tasks.get(m.id, []):
            owner = (r["owner"] or "").strip()
            owner = "" if owner.casefold() in {"не указано", "не указан"} else owner
            dl = (r["deadline"] or "").strip()
            iso = dl if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dl) else ""
            if r["status"] == "done":
                f.done.append({"text": _clean(r["text"]), "owner": owner})
            else:
                f.open.append({"text": _clean(r["text"]), "owner": owner, "due": iso,
                               "dueText": "" if iso or dl.casefold() in {"", "не указан", "не указано"} else dl,
                               "overdue": bool(iso) and iso < today_iso, "draft": r["status"] == "draft"})
        f.risks = [_clean(x["text"]) for x in knowledge.fact_items(m, "Риски")][:3]
        f.questions = [_clean(x["text"]) for x in knowledge.fact_items(m, "Открытые вопросы")][:3]
        out.append(f)
    return out


def stats(facts: list[MeetingFacts]) -> dict:
    open_tasks = [t for f in facts for t in f.open]
    return {
        "records": len(facts),
        "decisions": sum(len(f.decisions) for f in facts),
        "decisionsDraft": sum(1 for f in facts for d in f.decisions if d["draft"]),
        "tasksDone": sum(len(f.done) for f in facts),
        "tasksOpen": len(open_tasks),
        "tasksOverdue": sum(1 for t in open_tasks if t["overdue"]),
        "risks": sum(len(f.risks) for f in facts),
    }


# --------------------------------------------------------------------------- текст

def title_line(intent: Intent) -> str:
    parts = [intent.project["title"] if intent.project else "", intent.person["name"] if intent.person else ""]
    who = " · ".join(p for p in parts if p) or "Все проекты"
    span = short(intent.since.isoformat()) if intent.since == intent.until else f"{short(intent.since.isoformat())} – {short(intent.until.isoformat())}"
    return f"{who} · {intent.label} ({span})"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {word}"


def stats_line(st: dict) -> str:
    bits = [_plural(st["records"], "запись", "записи", "записей"), _plural(st["decisions"], "решение", "решения", "решений")]
    tasks = f"задач: {st['tasksDone']} выполнено, {st['tasksOpen']} открыто"
    if st["tasksOverdue"]:
        tasks += f", из них просрочено {st['tasksOverdue']}"
    return ", ".join(bits + [tasks])


def _more(items: list[str], limit: int) -> list[str]:
    if len(items) <= limit:
        return items
    return items[:limit] + [f"…и ещё {len(items) - limit}"]


def structured_text(intent: Intent, facts: list[MeetingFacts]) -> str:
    """Сводка без модели: те же факты, но списками."""
    st = stats(facts)
    multi = intent.project is None

    def where(f: MeetingFacts) -> str:
        return short(f.date) + (f", {f.project}" if multi and f.project else "")

    blocks = [f"**{title_line(intent)}**\n{stats_line(st)}."]
    dec = [f"{d['text']}{' (не подтверждено)' if d['draft'] else ''} — {where(f)}" for f in facts for d in f.decisions]
    if dec:
        blocks.append("**Решения**\n" + "\n".join("- " + x for x in _more(dec, 8)))
    done = [f"{t['text']}{' — ' + t['owner'] if t['owner'] else ''} ({where(f)})" for f in facts for t in f.done]
    if done:
        blocks.append("**Выполнено**\n" + "\n".join("- " + x for x in _more(done, 8)))
    opened = []
    for f in facts:
        for t in f.open:
            when = short(t["due"]) if t["due"] else t["dueText"]
            tail = ", ".join(x for x in [t["owner"], ("до " + when) if when else "", "просрочено" if t["overdue"] else "",
                                         "черновик" if t["draft"] else ""] if x)
            opened.append(t["text"] + (f" — {tail}" if tail else ""))
    if opened:
        blocks.append("**В работе**\n" + "\n".join("- " + x for x in _more(opened, 8)))
    risks = [f"{r} ({where(f)})" for f in facts for r in f.risks]
    if risks:
        blocks.append("**Риски**\n" + "\n".join("- " + x for x in _more(risks, 5)))
    questions = [f"{q} ({where(f)})" for f in facts for q in f.questions]
    if questions:
        blocks.append("**Открытые вопросы**\n" + "\n".join("- " + x for x in _more(questions, 5)))
    if not (dec or done or opened or risks or questions):
        talk = [f"{f.summary[0]} ({where(f)})" for f in facts if f.summary]
        if talk:
            blocks.append("**О чём говорили**\n" + "\n".join("- " + x for x in _more(talk, 8)))
    return "\n\n".join(blocks)


def facts_text(f: MeetingFacts, multi: bool) -> str:
    lines = [f"Встреча {short(f.date)}: {f.title}" + (f" [проект: {f.project}]" if multi and f.project else "")]
    lines += ["  о чём: " + s for s in f.summary[:2]]
    lines += [f"  решение{' (не подтверждено)' if d['draft'] else ''}: {d['text']}" for d in f.decisions]
    lines += [f"  задача выполнена: {t['text']}" + (f" ({t['owner']})" if t["owner"] else "") for t in f.done]
    for t in f.open:
        when = short(t["due"]) if t["due"] else t["dueText"]
        lines.append(f"  задача в работе{' (черновик)' if t['draft'] else ''}: {t['text']}" + (f" ({t['owner']})" if t["owner"] else "")
                     + (f", срок {when}" if when else "") + (", ПРОСРОЧЕНА" if t["overdue"] else ""))
    lines += ["  риск: " + r for r in f.risks]
    lines += ["  вопрос: " + q for q in f.questions]
    return "\n".join(lines)


def chunks(facts: list[MeetingFacts], multi: bool, budget: int = BUDGET) -> list[str]:
    out, current, size = [], [], 0
    for f in facts:
        text = facts_text(f, multi)
        if current and size + len(text) > budget:
            out.append("\n".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text) + 1
    if current:
        out.append("\n".join(current))
    return out


RULES = """Правила:
- Пиши только на основании фактов ниже, ничего не выдумывай.
- «Сделано» — только задачи с пометкой «выполнена» и решения; задачи «в работе» и обсуждения не называй сделанными.
- Помечай, что не подтверждено (черновики), и что просрочено.
- Без меток встреч, без вступлений, без рассуждений. Названия встреч не повторяй, достаточно даты.
- По-русски, кратко и конкретно."""


def prompt_final(intent: Intent, st: dict, body: str) -> str:
    return f"""Ты помощник по архиву рабочих встреч. Составь итоговую сводку.
Область: {title_line(intent)}
Цифры: {stats_line(st)}.

{RULES}

Формат ответа (Markdown, только эти блоки, пустые пропусти):
**Главное** — 1–2 предложения.
**Сделано и решено** — до 6 пунктов.
**В работе** — до 5 пунктов, с ответственным и сроком, если они есть.
**Риски и вопросы** — до 4 пунктов.

Факты:
{body}

СВОДКА:
"""


def prompt_part(intent: Intent, body: str) -> str:
    return f"""Ты помощник по архиву рабочих встреч. Ниже часть фактов за период «{intent.label}».
Сожми их в список из 6–10 коротких пунктов, сохранив даты, ответственных, сроки и статусы (выполнена, в работе, просрочена, не подтверждено).

{RULES}

Факты:
{body}

ПУНКТЫ:
"""


def generate(prompt: str, num_predict: int = 700) -> str:
    """Запрос к Ollama. Вынесен в функцию модуля, чтобы подменять в тестах. Бросает OSError, если модель недоступна."""
    payload = {"model": config.llm_model(), "stream": False, "raw": True, "prompt": prompt,
               "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": num_predict}}
    request = urllib.request.Request(f"{config.OLLAMA_URL}/api/generate", data=json.dumps(payload, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            raw = json.load(response).get("response", "")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise OSError(str(getattr(exc, "reason", exc))) from exc
    if "</think>" in raw:
        raw = raw.rsplit("</think>", 1)[-1]
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    return knowledge.clean_answer(raw)


def _squeeze(intent: Intent, parts: list[str]) -> list[str]:
    """Частичные итоги жмём ещё раз, пока они не поместятся в один запрос."""
    while len("\n".join(parts)) > BUDGET and len(parts) > 1:
        merged, group = [], []
        for p in parts:
            if group and len("\n".join(group + [p])) > BUDGET:
                merged.append(generate(prompt_part(intent, "\n".join(group)), 450))
                group = []
            group.append(p)
        merged.append(generate(prompt_part(intent, "\n".join(group)), 450))
        if len(merged) >= len(parts):
            return merged
        parts = merged
    return parts


def write(intent: Intent, facts: list[MeetingFacts]) -> tuple[str, str]:
    """Возвращает (текст, режим): 'model' или 'structured'."""
    st = stats(facts)
    parts = chunks(facts, intent.project is None)
    try:
        if len(parts) > 1:
            parts = [generate(prompt_part(intent, p), 450) for p in parts]
            if not all(parts):
                raise OSError("пустой ответ модели")
            parts = _squeeze(intent, parts)
        text = generate(prompt_final(intent, st, "\n".join(parts)))
        if not text:
            raise OSError("пустой ответ модели")
    except OSError as exc:
        return structured_text(intent, facts) + f"\n\n_Сводка собрана без модели: Ollama недоступна ({exc})._", "structured"
    return f"**{title_line(intent)}**\n{stats_line(st)}.\n\n{text}", "model"


def build(core, question: str, scope: dict, date_of: Callable, today: date | None = None) -> dict | None:
    """Ответ-сводка или None, если вопрос не обзорный."""
    intent = detect(core, question, scope, today)
    if intent is None:
        return None
    meetings = select_meetings(core, intent, date_of)
    if not meetings and not intent.explicit_period:
        # период не назван, а за 30 дней записей нет: берём всё время, а не отвечаем «ничего нет»
        intent = Intent(date(2000, 1, 1), intent.until, "всё время", False, intent.project, intent.person)
        meetings = select_meetings(core, intent, date_of)
        if meetings:
            intent.since = min(date.fromisoformat(date_of(m)) for m in meetings)
    span = f"{short(intent.since.isoformat())} – {short(intent.until.isoformat())}"
    if not meetings:
        who = (" по проекту «" + intent.project["title"] + "»") if intent.project else (" с участием " + intent.person["name"]) if intent.person else ""
        return {"answer": f"За период «{intent.label}» ({span}) записей{who} нет. Попробуйте другой период или область поиска.",
                "sources": [], "mode": "сводка", "confirmed": False,
                "digest": {"title": title_line(intent), "stats": stats([]), "records": 0, "shown": 0, "model": False}}
    facts = gather(meetings, date_of, today)
    text, how = write(intent, facts)
    sources = [{"meetingId": f.id, "title": f.title, "date": f.date, "time": "", "section": "Сводка", "t": "", "tSec": None,
                "excerpt": f.decisions[0]["text"] if f.decisions else f.summary[0] if f.summary else f.title}
               for f in reversed(facts[-MAX_SOURCES:])]
    return {"answer": text, "sources": sources, "mode": "сводка" if how == "model" else "сводка без модели", "confirmed": True,
            "digest": {"title": title_line(intent), "stats": stats(facts), "records": len(facts), "shown": len(sources), "model": how == "model"}}
