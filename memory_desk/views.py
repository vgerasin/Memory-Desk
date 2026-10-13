"""«Взгляды» на запись: дополнительные разделы заметки с другим фокусом («только решения», «риски», «для руководителя»).

Каждый взгляд — отдельный раздел `## Взгляд: <название>` перед «Стенограммой». Основное саммари и остальные разделы не
меняются. Под заголовком стоит служебная строка с источником (фокус, дата, модель) и отпечаток текста: если раздел
правили руками, пересчёт спрашивает подтверждения и не затирает правки молча. Взгляд строится по стенограмме, пункты
идут с таймкодами и попадают в поиск и чат так же, как остальные разделы заметки."""
from __future__ import annotations

import hashlib
import math
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config, knowledge, storage

PREFIX = "Взгляд: "
MAX_VIEWS = 8
MAX_TITLE = 60
MAX_INSTRUCTION = 400


@dataclass(frozen=True)
class Preset:
    id: str
    title: str
    instruction: str
    limit: int


PRESETS: tuple[Preset, ...] = (
    Preset("decisions", "Только решения",
           "Выпиши только принятые решения и договорённости. Обсуждения, предложения без согласия и задачи не включай.", 15),
    Preset("tasks_by_person", "Задачи по людям",
           "Выпиши поручения и обязательства, каждый пункт в виде «Имя — что сделать (срок, если назван)». "
           "Имя пиши, только если оно названо прямо; иначе «Не назначено».", 20),
    Preset("client_questions", "Вопросы к заказчику",
           "Выпиши вопросы и неясности, которые нужно уточнить у заказчика или внешней стороны: недостающие данные, "
           "требования, сроки, доступы.", 12),
    Preset("risks", "Риски",
           "Выпиши явно названные риски, угрозы срокам и качеству, зависимости и проблемы. Каждый пункт коротко: в чём риск.", 12),
    Preset("exec5", "Для руководителя на 5 строк",
           "Составь не больше пяти коротких строк для руководителя: главный итог, ключевое решение, главный риск, "
           "что нужно от руководителя, ближайший шаг. Без подробностей.", 5),
)
PRESET_BY_ID = {p.id: p for p in PRESETS}


class ViewError(RuntimeError):
    """Взгляд не построен; текст понятен пользователю. `status` — код ответа API."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def public_presets() -> list[dict]:
    return [{"id": p.id, "title": p.title, "instruction": p.instruction, "limit": p.limit} for p in PRESETS]


# --------------------------------------------------------------------------- разбор и запись раздела

_MARKER = re.compile(r"<!--\s*md-view\s+(.*?)\s*-->")
_ATTR = re.compile(r"(\w+)=(\S*)")
_SECTION = re.compile(r"(?ms)^##[ \t]+" + re.escape(PREFIX) + r".*?(?=^##[ \t]+|\Z)")


def _digest(body: str) -> str:
    return hashlib.md5("\n".join(l.rstrip() for l in body.strip().splitlines()).encode("utf-8")).hexdigest()[:8]


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="").replace("-", "%2D")  # «--» внутри комментария Markdown недопустимо


def _unquote(value: str) -> str:
    return urllib.parse.unquote(value)


def parse_sections(content: str) -> list[dict]:
    """Взгляды заметки: id, название, фокус, дата, модель, пункты и признак ручной правки."""
    views = []
    for match in _SECTION.finditer(content):
        block = match.group(0)
        heading, _, rest = block.partition("\n")
        title = heading.strip().lstrip("#").strip()[len(PREFIX):].strip()
        marker = _MARKER.search(rest)
        attrs = {k: _unquote(v) for k, v in _ATTR.findall(marker.group(1))} if marker else {}
        body_lines = [l for l in rest.splitlines() if not _MARKER.search(l) and not re.match(r"^_Взгляд ", l.strip())]
        body = "\n".join(body_lines).strip()
        view_id = attrs.get("id") or "x-" + hashlib.md5(title.encode()).hexdigest()[:6]
        items = []
        for line in storage.bullet_lines(body):
            fact = storage.parse_fact(line)
            if fact["text"]:
                items.append({"text": fact["text"], "t": fact["evidence_time"], "tSec": knowledge.to_seconds(fact["evidence_time"])})
        views.append({
            "id": view_id, "title": title, "kind": attrs.get("kind", "custom"), "instruction": attrs.get("instr", ""),
            "date": attrs.get("date", ""), "model": attrs.get("model", ""), "items": items,
            "edited": bool(marker) and attrs.get("hash", "") != _digest(body), "body": body,
        })
    return views


def list_views(meeting: knowledge.Meeting) -> list[dict]:
    """Взгляды записи для интерфейса (без служебного текста раздела)."""
    return [{k: v for k, v in view.items() if k != "body"} for view in parse_sections(meeting.content)]


def render_section(view_id: str, kind: str, title: str, instruction: str, items: list[dict], model: str,
                   when: datetime | None = None) -> str:
    when = when or datetime.now()
    lines = []
    for item in items:
        stamp = f" — `⏱ {item['evidence_time']}`" if item.get("evidence_time") else ""
        lines.append(f"- {item['text']}{stamp}")
    body = "\n".join(lines) or "Не выявлено."
    marker = (f"<!-- md-view id={view_id} kind={kind} date={when:%Y-%m-%d} model={_quote(model)} "
              f"hash={_digest(body)} instr={_quote(instruction) if kind == 'custom' else ''} -->")
    source = f"_Взгляд «{title}» · {when:%d.%m.%Y} · {model} · по стенограмме_"
    return f"## {PREFIX}{title}\n\n{marker}\n{source}\n\n{body}\n"


def _write(meeting: knowledge.Meeting, text: str) -> None:
    temp = meeting.path.with_name(f".{meeting.path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(meeting.path)


def put_section(meeting: knowledge.Meeting, view_id: str, section: str) -> None:
    """Вставляет или заменяет раздел взгляда. Остальной текст заметки не затрагивается."""
    text = meeting.path.read_text(encoding="utf-8")
    existing = next((m for m in _SECTION.finditer(text) if _section_id(m.group(0)) == view_id), None)
    if existing:
        text = text[:existing.start()] + section.rstrip() + "\n\n" + text[existing.end():].lstrip("\n")
    else:
        tail = re.search(r"(?m)^##[ \t]+(?:Полная )?[Сс]тенограмма[ \t]*$", text)
        if tail:
            text = text[:tail.start()] + section.rstrip() + "\n\n" + text[tail.start():]
        else:
            text = text.rstrip() + "\n\n" + section.rstrip() + "\n"
    _write(meeting, text)


def _section_id(block: str) -> str:
    marker = _MARKER.search(block)
    attrs = dict(_ATTR.findall(marker.group(1))) if marker else {}
    if attrs.get("id"):
        return attrs["id"]
    title = block.partition("\n")[0].strip().lstrip("#").strip()[len(PREFIX):].strip()
    return "x-" + hashlib.md5(title.encode()).hexdigest()[:6]


def remove_section(meeting: knowledge.Meeting, view_id: str) -> bool:
    text = meeting.path.read_text(encoding="utf-8")
    found = next((m for m in _SECTION.finditer(text) if _section_id(m.group(0)) == view_id), None)
    if not found:
        return False
    _write(meeting, text[:found.start()] + text[found.end():].lstrip("\n"))
    return True


def sections_text(content: str) -> str:
    """Все разделы взглядов подряд — чтобы пересборка заметки по шаблону их не теряла."""
    return "\n".join(m.group(0).rstrip() + "\n" for m in _SECTION.finditer(content))


# --------------------------------------------------------------------------- запрос к модели

PROMPT = """
Ты — аккуратный секретарь. По тексту стенограммы составь «взгляд» на запись с заданным фокусом.

Фокус: {title}.
{instruction}

Правила:
- Используй только то, что есть в тексте. Ничего не придумывай: ни имён, ни решений, ни сроков.
- Метки вида [00:01:16] — позиции в аудио, а не даты. Метки «Спикер 1», «Спикер 2» — не имена.
- Для каждого пункта укажи evidence_time — ближайшую к нему метку времени из текста. Нет метки — пункт не включай.
- Пунктов не больше {limit}. Если подходящего в тексте нет, верни пустой массив.

Верни только JSON: {{"items": [{{"text": "пункт", "evidence_time": "HH:MM:SS"}}]}}

Текст:
{transcript}
""".strip()

CONDENSE = """
Ниже пункты, собранные из разных частей одной записи, по фокусу «{title}». {instruction}
Объедини повторы и оставь самое важное, не больше {limit} пунктов. Ничего не добавляй, у каждого пункта сохрани
evidence_time из исходного.

Верни только JSON: {{"items": [{{"text": "пункт", "evidence_time": "HH:MM:SS"}}]}}

Пункты:
{items}
""".strip()


def _worker():
    from . import rebuild
    return rebuild._worker()


def _ask(prompt: str) -> dict:
    from . import llm
    return llm.ollama_json(prompt, model=config.OLLAMA_MODEL, url=config.OLLAMA_URL, num_ctx=8192, num_predict=1500,
                           attempts=2, timeout=300)


def _items(data: dict, transcript: str, limit: int) -> list[dict]:
    mp = _worker()
    values = data.get("items") if isinstance(data, dict) else None
    return mp.normalize_facts(values, transcript)[:limit * 3]


def build_items(transcript: str, title: str, instruction: str, limit: int, progress=None) -> list[dict]:
    """Пункты взгляда по стенограмме. Длинная запись разбирается частями, затем пункты сводятся к лимиту."""
    mp = _worker()
    chunks = mp.split_transcript(transcript) or [transcript]
    found: list[dict] = []
    seen: set[str] = set()
    for index, chunk in enumerate(chunks, 1):
        if progress:
            progress(index, len(chunks))
        prompt = PROMPT.format(title=title, instruction=instruction, limit=limit, transcript=chunk)
        for item in _items(_ask(prompt), chunk, limit):
            key = " ".join(item["text"].casefold().split())
            if key not in seen:
                seen.add(key)
                found.append({"text": item["text"], "evidence_time": item.get("evidence_time", "")})
    if len(found) > limit:
        if len(chunks) > 1:
            if progress:
                progress(len(chunks) + 1, len(chunks) + 1)
            numbered = "\n".join(f"- {i['text']}" + (f" [{i['evidence_time']}]" if i["evidence_time"] else "") for i in found)
            data = _ask(CONDENSE.format(title=title, instruction=instruction, limit=limit, items=numbered[:9000]))
            condensed = _items(data, " ".join(f"[{i['evidence_time']}]" for i in found if i["evidence_time"]), limit)
            if condensed:
                found = [{"text": i["text"], "evidence_time": i.get("evidence_time", "")} for i in condensed]
        found = found[:limit]
    return found


# --------------------------------------------------------------------------- спецификация и запуск

def resolve(spec: dict) -> tuple[str, str, str, str, int]:
    """(id, вид, название, фокус, лимит) по пресету или своему описанию."""
    preset_id = str(spec.get("presetId") or "")
    if preset_id:
        preset = PRESET_BY_ID.get(preset_id)
        if not preset:
            raise ViewError("Такого взгляда нет в наборе", 404)
        return preset.id, "preset", preset.title, preset.instruction, preset.limit
    title = " ".join(str(spec.get("title") or "").split())[:MAX_TITLE].lstrip("#").strip()
    instruction = " ".join(str(spec.get("instruction") or "").split())[:MAX_INSTRUCTION]
    if not title or len(instruction) < 8:
        raise ViewError("Укажите название и одной-двумя фразами, что выписывать")
    if title.casefold().startswith(PREFIX.strip().casefold()):
        title = title[len(PREFIX):].strip()
    from . import templates
    slug = templates.new_id(title)  # латиница из названия
    limit = max(3, min(30, int(spec.get("limit") or 12)))
    return "c-" + slug[:28], "custom", title, instruction, limit


JOBS: dict[str, dict] = {}
_lock = threading.Lock()


def _percent(job: dict, now: float) -> int:
    if job["state"] == "done":
        return 100
    total = max(1, job["total"])
    done_part = (job["part"] - 1) / total
    # внутри части процент плавно растёт и не доходит до её конца: время ответа модели заранее неизвестно
    within = 1 - math.exp(-max(0.0, now - job["partStarted"]) / 12.0)
    return min(97, int(5 + 90 * (done_part + within / total * 0.95)))


def status(meeting_id: str) -> dict:
    job = JOBS.get(meeting_id)
    if not job:
        return {"state": "idle"}
    now = time.time()
    view = {"state": job["state"], "viewId": job["viewId"], "title": job["title"], "label": job["label"],
            "percent": _percent(job, now), "part": job["part"], "total": job["total"],
            "elapsed": int((job.get("finishedAt") or now) - job["startedAt"])}
    if job["state"] == "error":
        view["message"] = job["message"]
    return view


def start(core, meeting_id: str, spec: dict, *, refresh: bool = False, force: bool = False) -> dict:
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise ViewError("Запись не найдена", 404)
    if refresh:
        view = next((v for v in parse_sections(meeting.content) if v["id"] == str(spec.get("viewId") or "")), None)
        if not view:
            raise ViewError("Такого взгляда в записи нет", 404)
        if view["edited"] and not force:
            raise ViewError("Раздел правили вручную: пересчёт заменит ваши правки. Подтвердите замену.", 409)
        if view["id"] in PRESET_BY_ID:
            view_id, kind, title, instruction, limit = resolve({"presetId": view["id"]})
        else:
            view_id, kind, title = view["id"], "custom", view["title"]
            instruction, limit = view["instruction"] or view["title"], max(3, min(30, len(view["items"]) or 12))
    else:
        view_id, kind, title, instruction, limit = resolve(spec)
        existing = parse_sections(meeting.content)
        if any(v["id"] == view_id for v in existing):
            raise ViewError("Такой взгляд уже есть: используйте «Пересчитать»", 409)
        if len(existing) >= MAX_VIEWS:
            raise ViewError(f"Взглядов у записи не больше {MAX_VIEWS}: удалите ненужный", 400)
    transcript = knowledge.meeting_transcript(meeting).strip()
    if len(transcript) < 20:
        raise ViewError("У записи нет стенограммы: взгляд строить не из чего")
    with _lock:
        if any(j["state"] == "running" for j in JOBS.values()):
            raise ViewError("Другой взгляд уже строится: дождитесь окончания", 409)
        now = time.time()
        job = {"state": "running", "viewId": view_id, "title": title, "label": f"Строю взгляд «{title}»", "part": 1,
               "total": 1, "startedAt": now, "partStarted": now}
        JOBS[meeting_id] = job

    def progress(part: int, total: int) -> None:
        job.update(part=part, total=total, partStarted=time.time(),
                   label=(f"Читаю часть {part} из {total}" if total > 1 and part <= total else
                          "Свожу пункты вместе" if total > 1 else f"Строю взгляд «{title}»"))

    def work() -> None:
        try:
            items = build_items(transcript, title, instruction, limit, progress)
            model = config.OLLAMA_MODEL
            put_section(meeting, view_id, render_section(view_id, kind, title, instruction, items, model))
            core.reload(force=True)
            job["state"] = "done"
        except Exception as exc:  # noqa: BLE001 — причина должна дойти до интерфейса
            job.update(state="error", message=str(exc))
        finally:
            job["finishedAt"] = time.time()

    threading.Thread(target=work, name="view", daemon=True).start()
    return status(meeting_id)


def delete(core, meeting_id: str, view_id: str) -> bool:
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise ViewError("Запись не найдена", 404)
    removed = remove_section(meeting, view_id)
    if removed:
        core.reload(force=True)
    return removed
