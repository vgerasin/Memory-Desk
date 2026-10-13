"""Предложения действий после встречи: событие в Календаре, черновик письма, напоминание.

Кабинет только **предлагает**. Без нажатия «Создать» не меняется ничего: ни Календарь, ни «Напоминания», ни «Почта», ни
заметка. Предложения лежат в базе кабинета (таблица `suggestions`), поэтому заметка Obsidian остаётся как была.

Как находятся предложения:
1. Код отбирает реплики, где есть намерение (созвониться, отправить, напомнить) и, для событий, названа дата или время.
   Нет таких реплик — модель не вызывается вовсе, предложений нет.
2. Модель по этим репликам выделяет вид действия, адресата и короткое название. **Дат она не называет.**
3. Дату и время разрешает код (`whenru`) по дате записи. Неоднозначное («в следующий вторник» в понедельник,
   «через пару дней», две даты в одной реплике) остаётся пустым: карточка просит «Изменить», а не гадает.
4. Каждое предложение привязано к реплике (таймкод); выдуманный моделью таймкод отбрасывается. Ниже порога уверенности
   (`THRESHOLD`) предложения не показываются, больше `MAX_CARDS` на запись не бывает.

Если модель недоступна, работают строгие правила (`engine = "rules"`), они осторожнее модели.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, datetime, time, timedelta

from . import apple, audit, config, integrations, knowledge, mailer, storage, whenru

KINDS = {"event": "Событие в Календаре", "mail": "Черновик письма", "reminder": "Напоминание"}
STATUSES = ("new", "created", "dismissed")
THRESHOLD = 0.6
MAX_CARDS = 6
MAX_WINDOWS = 14
PRE = {"event": 0.65, "reminder": 0.7, "mail": 0.65}     # уверенность правил, когда модели нет

_EVENT = re.compile(r"созвон|встретим|увидимся|соберемся|собираемся|назначим|договоримся|следующ\w+\s+(?:встреч|созвон)|"
                    r"давайте\s+(?:встретимся|созвонимся|соберемся)")
_REMIND = re.compile(r"напомни|напоминани|не\s+забуд|не\s+забыть|поставь\w*\s+(?:мне\s+)?напоминани")
_SEND = re.compile(r"\b(?:отправ|пришл|вышл|скин|перешл)\w*")
_SENT_OBJECT = re.compile(r"протокол|письм|итог|материал|презентац|резюме")
_HEDGE = re.compile(r"\b(?:может быть|возможно|если получится|если успе|не факт|вряд ли|было бы здорово|когда-нибудь)\b")

_scans: dict[str, dict] = {}
_scans_lock = threading.Lock()


# --------------------------------------------------------------------------- вход

def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def people_of(meeting_id: str) -> list[dict]:
    rows = storage.rows("SELECT p.name, p.aliases, p.notes FROM meeting_people mp JOIN people p ON p.id=mp.person_id "
                        "WHERE mp.meeting_id=? ORDER BY p.name", (meeting_id,))
    out = []
    for row in rows:
        try:
            aliases = [str(a) for a in json.loads(row.get("aliases") or "[]")]
        except ValueError:
            aliases = []
        out.append({"name": row["name"], "aliases": aliases, "notes": row.get("notes") or ""})
    return out


def _stem(word: str) -> str:
    word = _norm(word)
    return word[: max(3, len(word) - 2)] if len(word) > 4 else word


def mention_pos(text: str, person: dict) -> int:
    """Позиция, где в реплике назван человек (-1, если не назван); русские падежи («Анне», «Ивану») ловятся по основе слова."""
    haystack = _norm(text)
    best = -1
    for name in [person["name"], *person["aliases"]]:
        first = (name.split() or [""])[0]
        found = re.search(r"\b" + re.escape(_stem(first)) + r"\w*", haystack) if first else None
        if found and (best < 0 or found.start() < best):
            best = found.start()
    return best


def mentioned(text: str, person: dict) -> bool:
    return mention_pos(text, person) >= 0


def email_of(person: dict | None) -> str:
    if not person:
        return ""
    found = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", person.get("notes", ""))
    return found.group(0) if found else ""


def base_day(meeting: knowledge.Meeting) -> date:
    for value in (meeting.date, (meeting.recorded_at or "")[:10]):
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
    return date.today()


def _flags(text: str, base: date) -> dict:
    norm = _norm(text)
    when = whenru.resolve(norm, base)
    return {"event": bool(_EVENT.search(norm)) and when.found, "remind": bool(_REMIND.search(norm)),
            "mail": bool(_SEND.search(norm)) and bool(_SENT_OBJECT.search(norm)), "hedge": bool(_HEDGE.search(norm))}


def candidate_windows(segments: list[dict], base: date) -> list[list[int]]:
    """Индексы реплик, где вообще может быть договорённость. Окно: сама реплика и следующая за ней."""
    hits = []
    for index, seg in enumerate(segments):
        flags = _flags(seg["text"], base)
        if (flags["event"] or flags["remind"] or flags["mail"]) and not flags["hedge"]:
            hits.append(index)
    windows: list[list[int]] = []
    for index in hits[:MAX_WINDOWS]:
        window = [index] + ([index + 1] if index + 1 < len(segments) else [])
        if windows and windows[-1][-1] >= window[0]:
            windows[-1] = sorted(set(windows[-1]) | set(window))
        else:
            windows.append(window)
    return windows


# --------------------------------------------------------------------------- модель

PROMPT = """
Ты помогаешь вести рабочие встречи. Ниже фрагменты стенограммы встречи «{title}» с таймкодами.
Найди только ЯВНЫЕ договорённости о конкретном будущем действии трёх видов:
- "event" — договорились о новой встрече или созвоне (с датой или временем);
- "mail" — кто-то должен отправить письмо, протокол, итоги или материалы конкретному человеку;
- "reminder" — просят напомнить или не забыть сделать что-то к сроку.
Не предлагай ничего для прошлого, для общих рассуждений, предположений («может быть», «если получится») и для задач,
которые не про встречу, письмо или напоминание. Если сомневаешься, не добавляй.
Дат и времени НЕ называй: их определит программа. Таймкод бери точно как в стенограмме.

Верни только JSON:
{{"items": [{{"kind": "event|mail|reminder", "evidence_time": "HH:MM:SS", "title": "короткое название до 80 знаков",
"addressee": "имя адресата или пустая строка", "confidence": 0.0}}]}}
Не больше {limit} пунктов. Если подходящего нет, верни {{"items": []}}.

Стенограмма:
{transcript}
""".strip()


def _ask(prompt: str) -> dict:
    from . import llm
    return llm.ollama_json(prompt, model=config.OLLAMA_MODEL, url=config.OLLAMA_URL, num_ctx=8192, num_predict=900,
                           attempts=2, timeout=300)


def _transcript_lines(segments: list[dict], windows: list[list[int]]) -> str:
    out = []
    for window in windows:
        for index in window:
            seg = segments[index]
            out.append(f"[{seg.get('t') or '00:00:00'}] {seg.get('speaker') or 'Говорящий'}: {seg['text']}")
        out.append("---")
    return "\n".join(out)


def _segment_at(segments: list[dict], windows: list[list[int]], stamp: str) -> int | None:
    allowed = sorted({i for w in windows for i in w})
    stamp = str(stamp or "").strip()
    for index in allowed:
        if segments[index].get("t") == stamp:
            return index
    try:
        parts = [int(x) for x in stamp.split(":")]
        sec = parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]
    except (ValueError, IndexError):
        return None
    near = [i for i in allowed if abs(float(segments[i].get("sec", 0)) - sec) <= 2]
    return near[0] if len(near) == 1 else None


# --------------------------------------------------------------------------- карточки

def suggestion_id(meeting_id: str, kind: str, stamp: str) -> str:
    return hashlib.sha1(f"{meeting_id}|{kind}|{stamp}".encode("utf-8")).hexdigest()[:16]


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _card(meeting: knowledge.Meeting, kind: str, segments: list[dict], index: int, people: list[dict], *,
          title: str, who: str, confidence: float, engine: str) -> dict:
    seg = segments[index]
    base = base_day(meeting)
    text = seg["text"]
    when = whenru.resolve(text, base)
    if not when.found and index + 1 < len(segments):          # «Созвонимся.» — «В четверг в три.»
        when = whenru.resolve(text + " " + segments[index + 1]["text"], base)
    window_text = " ".join(segments[i]["text"] for i in range(max(0, index - 1), min(len(segments), index + 2)))
    person = next((p for p in people if mentioned(text, p)), None) or next((p for p in people if mentioned(window_text, p)), None)
    if kind == "mail":
        # «Анна, отправь протокол Ивану»: Анна — тот, к кому обращаются, получатель назван после глагола
        verb = _SEND.search(_norm(text))
        after = [(mention_pos(text, p), p) for p in people if verb and mention_pos(text, p) > verb.start()]
        person = min(after, key=lambda x: x[0])[1] if after else (None if verb else person)
    if who:
        named = next((p for p in people if mentioned(who, p)), None)
        person = named or person
    name = person["name"] if person else ""
    if kind == "event":
        label = _short(title, 120) or f"Продолжение: {meeting.title}"
    elif kind == "mail":
        label = _short(title, 120) or (f"Письмо: {name}" if name else "Письмо с итогами встречи")
    else:
        label = _short(title, 200) or _short(text, 200)
    return {
        "id": suggestion_id(meeting.id, kind, seg.get("t") or str(index)), "meeting_id": meeting.id, "kind": kind, "status": "new",
        "title": label, "day": when.day.isoformat() if when.day else "", "clock": when.clock.strftime("%H:%M") if when.clock else "",
        "all_day": 0, "who": name, "notes": "", "quote": _short(text, 300), "evidence_time": seg.get("t") or "",
        "sec": float(seg.get("sec") or 0), "confidence": round(float(confidence), 2), "engine": engine,
        "deadline": 1 if when.deadline else 0, "ambiguous": 1 if when.ambiguous else 0,
    }


def supported(kind: str, segments: list[dict], index: int, base: date) -> bool:
    """Код сам проверяет, что в реплике, на которую сослалась модель, есть признаки названного действия.
    Намерение должно быть в самой реплике; дата и предмет письма могут быть в соседней (сказали «созвонимся», а время в следующей)."""
    here = _norm(segments[index]["text"])
    before = _norm(segments[index - 1]["text"]) if index > 0 else ""
    after = _norm(segments[index + 1]["text"]) if index + 1 < len(segments) else ""
    if kind == "event":
        return bool(_EVENT.search(here)) and whenru.resolve(here + " " + after, base).found
    if kind == "reminder":
        return bool(_REMIND.search(here))
    return bool(_SEND.search(here)) and bool(_SENT_OBJECT.search(before + " " + here + " " + after))


def from_model(meeting: knowledge.Meeting, segments: list[dict], windows: list[list[int]], people: list[dict], data: dict) -> list[dict]:
    items = data.get("items") if isinstance(data, dict) else None
    cards: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if confidence < THRESHOLD:
            continue
        index = _segment_at(segments, windows, item.get("evidence_time", ""))
        if index is None or _flags(segments[index]["text"], base_day(meeting))["hedge"]:
            continue
        kind = item["kind"]
        if not supported(kind, segments, index, base_day(meeting)):
            continue          # модель назвала действие, а в самой реплике его признаков нет
        if (kind, index) in seen:
            continue
        seen.add((kind, index))
        cards.append(_card(meeting, kind, segments, index, people, title=str(item.get("title", "")),
                           who=str(item.get("addressee", "")), confidence=min(1.0, confidence), engine="model"))
    return cards


def from_rules(meeting: knowledge.Meeting, segments: list[dict], windows: list[list[int]], people: list[dict]) -> list[dict]:
    base = base_day(meeting)
    cards: list[dict] = []
    for index in sorted({i for w in windows for i in w}):
        flags = _flags(segments[index]["text"], base)
        if flags["hedge"]:
            continue
        for kind, key in (("event", "event"), ("reminder", "remind"), ("mail", "mail")):
            if flags[key]:
                title = ""
                if kind == "reminder":
                    title = re.sub(r"^.*?(?:напомни\w*|не забуд\w*|не забыть|напоминани\w*)[\s,:–-]*(?:мне\s+|что\s+)?", "",
                                   segments[index]["text"], flags=re.IGNORECASE).strip(" .,!")
                cards.append(_card(meeting, kind, segments, index, people, title=title, who="", confidence=PRE[kind], engine="rules"))
    return cards


def extract(meeting: knowledge.Meeting, ask=None) -> tuple[list[dict], str]:
    """Предложения по записи и название движка («model», «rules» или «нет»). Ничего не записывает."""
    segments = [s for s in knowledge.transcript_segments(meeting) if s.get("text")]
    base = base_day(meeting)
    windows = candidate_windows(segments, base)
    if not windows:
        return [], "none"
    people = people_of(meeting.id)
    try:
        prompt = PROMPT.format(title=meeting.title, limit=MAX_CARDS, transcript=_transcript_lines(segments, windows))
        cards = from_model(meeting, segments, windows, people, (ask or _ask)(prompt))
        engine = "model"
    except RuntimeError:
        cards, engine = from_rules(meeting, segments, windows, people), "rules"
    cards.sort(key=lambda c: (-c["confidence"], c["sec"]))
    return cards[:MAX_CARDS], engine


# --------------------------------------------------------------------------- хранение

def _save(meeting_id: str, cards: list[dict]) -> int:
    """Обновляет предложения: созданные, отклонённые и правленные руками не трогаются, непоявившиеся новые убираются."""
    storage.initialize()
    now = storage.now_iso()
    with storage.connect() as db:
        existing = {r["id"]: dict(r) for r in db.execute("SELECT * FROM suggestions WHERE meeting_id=?", (meeting_id,))}
        found = {c["id"] for c in cards}
        for sid, row in existing.items():
            if sid not in found and row["status"] == "new" and not row["edited"]:
                db.execute("DELETE FROM suggestions WHERE id=?", (sid,))
        for card in cards:
            old = existing.get(card["id"])
            if old and (old["status"] != "new" or old["edited"]):
                continue
            db.execute(
                "INSERT INTO suggestions (id,meeting_id,kind,status,title,day,clock,all_day,who,notes,quote,evidence_time,sec,"
                "confidence,engine,deadline,ambiguous,edited,created_ref,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'',?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,day=excluded.day,clock=excluded.clock,who=excluded.who,"
                "quote=excluded.quote,confidence=excluded.confidence,engine=excluded.engine,deadline=excluded.deadline,"
                "ambiguous=excluded.ambiguous,updated_at=excluded.updated_at",
                (card["id"], meeting_id, card["kind"], "new", card["title"], card["day"], card["clock"], card["all_day"],
                 card["who"], card["notes"], card["quote"], card["evidence_time"], card["sec"], card["confidence"],
                 card["engine"], card["deadline"], card["ambiguous"], now, now))
    return len(cards)


def scan(core, meeting_id: str, ask=None, actor: str = "system") -> dict:
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise KeyError(meeting_id)
    cards, engine = extract(meeting, ask)
    count = _save(meeting_id, cards)
    audit.record(actor, "suggestion.scan", target="meeting", target_id=meeting_id,
                 detail=f"найдено: {count}, поиск: {'моделью' if engine == 'model' else 'по правилам' if engine == 'rules' else 'без кандидатов'}")
    return {"count": count, "engine": engine}


def scan_state(meeting_id: str) -> dict:
    with _scans_lock:
        return dict(_scans.get(meeting_id, {"state": "idle", "message": ""}))


def scan_async(core, meeting_id: str, ask=None, actor: str = "system") -> bool:
    """Запускает поиск в фоне (модель может думать минуты). False, если по этой записи он уже идёт."""
    with _scans_lock:
        if _scans.get(meeting_id, {}).get("state") == "running":
            return False
        _scans[meeting_id] = {"state": "running", "message": "Ищу договорённости…"}

    def run() -> None:
        try:
            result = scan(core, meeting_id, ask, actor)
            state = {"state": "done", "message": f"Найдено: {result['count']}", **result}
        except Exception as exc:  # noqa: BLE001 — фоновая задача не должна падать молча
            state = {"state": "error", "message": f"{type(exc).__name__}: {exc}"}
        with _scans_lock:
            _scans[meeting_id] = state
        core.data_version += 1
    threading.Thread(target=run, name="suggest", daemon=True).start()
    return True


# --------------------------------------------------------------------------- показ

def _label_day(day: str) -> str:
    try:
        value = date.fromisoformat(day)
    except ValueError:
        return ""
    names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    return f"{names[value.weekday()]}, {value.day} {months[value.month - 1]}"


def view(row: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    kind = row["kind"]
    needs: list[str] = []
    if kind == "event":
        if not row["day"]:
            needs.append("day")
        if not row["clock"] and not row["all_day"]:
            needs.append("time")
    past = False
    if row["day"]:
        try:
            day = date.fromisoformat(row["day"])
            if kind == "event" and row["clock"]:
                past = datetime.combine(day, time.fromisoformat(row["clock"])) < now
            else:
                past = day < now.date()
        except ValueError:
            needs.append("day")
    ready = row["status"] == "new" and not needs and not (past and kind != "mail")
    return {
        "id": row["id"], "meetingId": row["meeting_id"], "kind": kind, "kindLabel": KINDS[kind], "status": row["status"],
        "title": row["title"], "day": row["day"], "dayLabel": _label_day(row["day"]), "clock": row["clock"],
        "allDay": bool(row["all_day"]), "who": row["who"], "notes": row["notes"], "quote": row["quote"],
        "t": row["evidence_time"], "sec": row["sec"], "confidence": row["confidence"], "engine": row["engine"],
        "deadline": bool(row["deadline"]), "ambiguous": bool(row["ambiguous"]), "edited": bool(row["edited"]),
        "needs": needs, "past": past, "canCreate": ready, "createdRef": row["created_ref"],
    }


def for_meeting(meeting_id: str) -> dict:
    storage.initialize()
    rows = storage.rows("SELECT * FROM suggestions WHERE meeting_id=? ORDER BY CASE status WHEN 'new' THEN 0 WHEN 'created' THEN 1 ELSE 2 END, sec",
                        (meeting_id,))
    return {"items": [view(r) for r in rows], "scan": scan_state(meeting_id)}


def pending(core, days: int = 14, limit: int = 8) -> dict:
    """Новые предложения по свежим записям: для блока на «Сегодня»."""
    storage.initialize()
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = storage.rows("SELECT s.*, m.title AS meeting_title, m.meeting_date AS meeting_date FROM suggestions s JOIN meetings m ON m.id=s.meeting_id "
                        "WHERE s.status='new' AND m.archived=0 AND COALESCE(m.meeting_date,'')>=? ORDER BY m.meeting_date DESC, s.sec", (since,))
    items = [{**view(r), "meetingTitle": r["meeting_title"], "meetingDate": r["meeting_date"]} for r in rows]
    return {"total": len(items), "items": items[:limit]}


def get(sid: str) -> dict | None:
    return storage.one("SELECT * FROM suggestions WHERE id=?", (sid,))


# --------------------------------------------------------------------------- правка и действия

class SuggestError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def edit(sid: str, fields: dict) -> dict:
    row = get(sid)
    if not row:
        raise SuggestError("Предложение не найдено", 404)
    if row["status"] != "new":
        raise SuggestError("Это предложение уже обработано", 409)
    changes: dict = {}
    if "title" in fields:
        title = _short(fields["title"], 200)
        if not title:
            raise SuggestError("Название не может быть пустым")
        changes["title"] = title
    if "day" in fields:
        day = str(fields["day"] or "").strip()
        if day:
            try:
                date.fromisoformat(day)
            except ValueError as exc:
                raise SuggestError("Неверная дата") from exc
        changes["day"] = day
    if "clock" in fields:
        clock = str(fields["clock"] or "").strip()
        if clock:
            try:
                time.fromisoformat(clock)
            except ValueError as exc:
                raise SuggestError("Неверное время") from exc
            clock = clock[:5]
        changes["clock"] = clock
    if "allDay" in fields:
        changes["all_day"] = 1 if fields["allDay"] else 0
    if "who" in fields:
        changes["who"] = _short(fields["who"], 200)
    if "notes" in fields:
        changes["notes"] = str(fields["notes"] or "").strip()[:1000]
    if not changes:
        raise SuggestError("Нечего менять")
    changes.update(edited=1, ambiguous=0, updated_at=storage.now_iso())
    with storage.connect() as db:
        db.execute("UPDATE suggestions SET " + ",".join(f"{k}=?" for k in changes) + " WHERE id=?", (*changes.values(), sid))
    return view(get(sid))


def set_status(sid: str, status: str) -> dict:
    row = get(sid)
    if not row:
        raise SuggestError("Предложение не найдено", 404)
    if status == "dismissed" and row["status"] == "created":
        raise SuggestError("Действие уже выполнено, отклонить его нельзя", 409)
    with storage.connect() as db:
        db.execute("UPDATE suggestions SET status=?, updated_at=? WHERE id=?", (status, storage.now_iso(), sid))
    return view(get(sid))


def _plain(markdown: str, name: str) -> str:
    out = []
    for line in markdown.splitlines():
        if line.startswith(("# ", "> ", "**Тема:**", "**Кому:**")):
            continue
        line = re.sub(r"^#{2,6}\s*", "", line).replace("**", "")
        if len(line) > 2 and line.startswith("_") and line.endswith("_"):
            line = line[1:-1]
        out.append(line)
    text = "\n".join(out).strip()
    if name:
        text = text.replace("Коллеги, добрый день!", f"{name}, добрый день!", 1)
    return re.sub(r"\n{3,}", "\n\n", text)


def create(core, sid: str) -> dict:
    """Выполняет предложение. Единственное место, где что-то реально создаётся; вызывается только по нажатию «Создать»."""
    row = get(sid)
    if not row:
        raise SuggestError("Предложение не найдено", 404)
    if row["status"] == "created":
        raise SuggestError("Это предложение уже выполнено", 409)
    meeting = core.by_id.get(row["meeting_id"])
    if not meeting:
        raise SuggestError("Запись не найдена", 404)
    shown = view(row)
    url = f"http://127.0.0.1:{config.PORT}/records/{meeting.id}"
    kind = row["kind"]
    try:
        if kind == "event":
            if shown["needs"]:
                raise SuggestError("Укажите " + ("дату и время" if len(shown["needs"]) == 2 else "дату" if "day" in shown["needs"] else "время"))
            if shown["past"]:
                raise SuggestError("Эта дата уже прошла: измените её")
            day = date.fromisoformat(row["day"])
            start = datetime.combine(day, time.fromisoformat(row["clock"]) if row["clock"] else time(0, 0))
            names = ", ".join(p["name"] for p in people_of(meeting.id))
            notes = (row["notes"] + "\n" if row["notes"] else "") + f"Из записи «{meeting.title}»" + (f". Участники: {names}" if names else "")
            ref = integrations.create_event(core.cfg, title=row["title"], start=start, end=None, all_day=bool(row["all_day"]),
                                            notes=notes, url=url)
            message = "Событие добавлено в Календарь"
        elif kind == "reminder":
            be = apple.backend()
            if be.status().get("reminders") != "authorized":
                raise apple.AppleError("Нет доступа к «Напоминаниям»: разрешите его в настройках")
            due = date.fromisoformat(row["day"]) if row["day"] else None
            if due and due < date.today():
                raise SuggestError("Эта дата уже прошла: измените её")
            ref = be.create_reminder(core.cfg.get("reminders_list") or "Memory Desk", row["title"], due,
                                     (row["notes"] + "\n" if row["notes"] else "") + f"Из записи «{meeting.title}»", url)
            message = "Напоминание создано"
        else:
            from . import artifacts, services
            person = next((p for p in people_of(meeting.id) if row["who"] and p["name"] == row["who"]), None)
            subject = f"Итоги: {meeting.title}"
            try:
                built = artifacts.build(core, "followup", {"meetingId": meeting.id}, services.meeting_date, addressee=row["who"])
                body = _plain(built["markdown"], row["who"])
            except ValueError:      # в записи нет решений и задач с таймкодами: короткое письмо со ссылкой на запись
                body = f"{row['who'] or 'Коллеги'}, добрый день!\n\nНаправляю итоги встречи «{meeting.title}» от {meeting.date}.\n\nЗапись: {url}"
            to = [email_of(person)] if email_of(person) else []
            mailer.backend().draft(subject, body, to)
            ref = subject
            message = "Черновик письма открыт в «Почте»: проверьте и отправьте сами"
    except (apple.AppleError, mailer.MailError) as exc:
        raise SuggestError(str(exc), 409) from exc
    except ValueError as exc:
        raise SuggestError(str(exc), 400) from exc
    with storage.connect() as db:
        db.execute("UPDATE suggestions SET status='created', created_ref=?, updated_at=? WHERE id=?",
                   (str(ref)[:200], storage.now_iso(), sid))
    return {**view(get(sid)), "message": message}
