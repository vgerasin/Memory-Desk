"""Живое досье: то, что добавляется к странице человека и проекта сверх цифр (`dossier.py`).

Четыре части, и каждая устроена так, чтобы не потерять слова человека и не выдумать лишнего:

1. **Закреплённые факты.** Строка с экрана досье (вопрос, риск, решение, задача) или собственная заметка. Лежат в базе
   кабинета (`dossier_pins`) и обновлением досье не затрагиваются: пересчёт читает записи, закрепления только добавляет.
   У закреплённого из записи сохраняются запись и таймкод; своя заметка — без источника, и это написано.
2. **Висящие вопросы.** Открытые вопросы из заметок по *всем* записям человека или проекта собираются по смыслу слов
   (`series.similarity`); вопрос «висит», если звучал не менее чем на двух встречах (`HANGING_FROM`), последний раз — в одной
   из последних записей, и после этого не появилось похожее решение. Каждое упоминание — ссылка на запись и таймкод. «Закрыть»
   вопрос вручную можно (`dossier_mutes`): он уходит под спойлер и больше не считается висящим.
3. **О чём разговор.** Один короткий абзац по последним записям. Модель его только переформулирует по пунктам кратких
   содержаний; ответ с новыми цифрами или именами отбрасывается. Без модели (или с выключенной настройкой) остаётся
   структурный вариант из тех же пунктов. Просмотр досье модель не вызывает никогда: готовый абзац читается из базы, пока
   записи не изменились, иначе показывается структурный вариант с пометкой «устарело».
4. **Что изменилось.** По последним записям: сколько решений, задач, вопросов и рисков принесла каждая.

Обновление идёт по событию «запись обработана» (`refresh_for_meeting_async`) и по кнопке «Обновить».
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from collections import Counter
from datetime import date

from . import audit, digest, knowledge, series, services, storage

SCOPES = ("person", "project")
PIN_LIMIT = 30
PIN_TEXT = 400
PIN_KINDS = {"manual": "Моя заметка", "question": "Вопрос", "risk": "Риск", "decision": "Решение", "task": "Задача", "summary": "Из записи"}
HANGING_FROM = 2            # столько разных встреч, на которых звучал один вопрос
HANGING_WINDOW = 3          # последнее упоминание должно быть в одной из стольких последних записей
ANSWERED_SIMILARITY = 0.4   # похожесть вопроса и более позднего решения, после которой вопрос считается закрытым
HANGING_LIMIT = 8
GIST_MEETINGS = 4
GIST_BULLETS = 3
GIST_MAX = 420
CHANGES_LIMIT = 5

_state: dict[tuple[str, str], dict] = {}
_state_lock = threading.Lock()


def _now() -> str:
    return storage.now_iso()


def _scope(scope: str) -> str:
    if scope not in SCOPES:
        raise ValueError("Досье бывает у человека или проекта")
    return scope


def _clean(text: str, limit: int = PIN_TEXT) -> str:
    return " ".join(str(text or "").split())[:limit]


def _order(m: knowledge.Meeting) -> tuple:
    return (m.recorded_at or m.date or "", m.time or "")


# --------------------------------------------------------------------------- закрепления

def _meeting_info(core, meeting_id: str) -> dict | None:
    m = core.by_id.get(meeting_id)
    if not m:
        return None
    return {"meetingId": m.id, "meetingTitle": m.title, "date": services.meeting_date(m)}


def _pin_dto(core, row: dict) -> dict:
    source = _meeting_info(core, row["meeting_id"]) if row["meeting_id"] else None
    return {
        "id": row["id"], "text": row["text"], "kind": row["kind"], "kindLabel": PIN_KINDS.get(row["kind"], "Моя заметка"),
        "origin": row["origin"], "edited": bool(row["edited"]), "createdAt": row["created_at"],
        "source": ({**source, "sec": row["sec"]} if source else None),
        "sourceGone": bool(row["meeting_id"]) and source is None,
    }


def pins(core, scope: str, owner_id: str) -> list[dict]:
    rows = storage.rows("SELECT * FROM dossier_pins WHERE scope=? AND owner_id=? ORDER BY created_at, rowid", (_scope(scope), owner_id))
    return [_pin_dto(core, r) for r in rows]


def add_pin(core, scope: str, owner_id: str, text: str, *, kind: str = "manual", meeting_id: str = "", sec: float | None = None) -> dict:
    scope = _scope(scope)
    body = _clean(text)
    if not body:
        raise ValueError("Напишите, что закрепить")
    if meeting_id and meeting_id not in core.by_id:
        raise ValueError("Запись не найдена")
    if not meeting_id:
        kind, sec = "manual", None
    elif kind not in PIN_KINDS or kind == "manual":
        kind = "summary"
    if sec is not None:
        try:
            sec = max(0.0, float(sec))
        except (TypeError, ValueError):
            sec = None
    existing = storage.rows("SELECT * FROM dossier_pins WHERE scope=? AND owner_id=?", (scope, owner_id))
    for row in existing:
        if row["meeting_id"] == meeting_id and (row["origin"] or row["text"]).casefold() == body.casefold():
            return _pin_dto(core, row)
    if len(existing) >= PIN_LIMIT:
        raise ValueError(f"Закреплено уже {PIN_LIMIT}: уберите лишнее, чтобы добавить новое")
    pin_id = uuid.uuid4().hex[:12]
    now = _now()
    with storage.connect() as db:
        db.execute("INSERT INTO dossier_pins (id,scope,owner_id,text,origin,kind,meeting_id,sec,edited,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,0,?,?)",
                   (pin_id, scope, owner_id, body, body if meeting_id else "", kind, meeting_id, sec, now, now))
    return _pin_dto(core, storage.one("SELECT * FROM dossier_pins WHERE id=?", (pin_id,)))


def get_pin(pin_id: str) -> dict | None:
    return storage.one("SELECT * FROM dossier_pins WHERE id=?", (pin_id,))


def edit_pin(core, pin_id: str, text: str) -> dict:
    row = get_pin(pin_id)
    if not row:
        raise KeyError("Закреплённый факт не найден")
    body = _clean(text)
    if not body:
        raise ValueError("Текст не может быть пустым")
    with storage.connect() as db:
        db.execute("UPDATE dossier_pins SET text=?, edited=1, updated_at=? WHERE id=?", (body, _now(), pin_id))
    return _pin_dto(core, get_pin(pin_id))


def remove_pin(pin_id: str) -> dict:
    row = get_pin(pin_id)
    if not row:
        raise KeyError("Закреплённый факт не найден")
    with storage.connect() as db:
        db.execute("DELETE FROM dossier_pins WHERE id=?", (pin_id,))
    return row


def forget_meeting(meeting_id: str, db=None) -> int:
    """Стирание записи: всё, что досье взяло из неё (закрепления из записи, закрытые вопросы, абзацы «о чём разговор»), уходит
    вместе с ней. Собственные заметки человека остаются. Возвращает число удалённых строк."""
    def run(conn) -> int:
        n = conn.execute("DELETE FROM dossier_pins WHERE meeting_id=?", (meeting_id,)).rowcount
        n += conn.execute("DELETE FROM dossier_mutes WHERE meeting_id=?", (meeting_id,)).rowcount
        n += conn.execute("DELETE FROM dossier_gists WHERE sources LIKE ?", (f'%"meetingId": "{meeting_id}"%',)).rowcount
        return n
    if db is not None:
        return run(db)
    with storage.connect() as conn:
        return run(conn)


def forget_count(meeting_id: str) -> int:
    n = 0
    for sql, arg in (("SELECT COUNT(*) n FROM dossier_pins WHERE meeting_id=?", meeting_id),
                     ("SELECT COUNT(*) n FROM dossier_mutes WHERE meeting_id=?", meeting_id),
                     ("SELECT COUNT(*) n FROM dossier_gists WHERE sources LIKE ?", f'%"meetingId": "{meeting_id}"%')):
        row = storage.one(sql, (arg,))
        n += int(row["n"]) if row else 0
    return n


# --------------------------------------------------------------------------- висящие вопросы

def _mutes(scope: str, owner_id: str) -> list[dict]:
    return storage.rows("SELECT * FROM dossier_mutes WHERE scope=? AND owner_id=? ORDER BY created_at, rowid", (_scope(scope), owner_id))


def mute_question(scope: str, owner_id: str, text: str, meeting_id: str = "") -> dict:
    """`meeting_id` — запись, из которой взята формулировка: при стирании записи вместе с ней уходит и закрытый вопрос."""
    scope, body = _scope(scope), _clean(text, 300)
    if not body:
        raise ValueError("Не указан вопрос")
    key = series.tokens(body)
    for row in _mutes(scope, owner_id):
        if series.similarity(key, series.tokens(row["text"])) >= series.QUESTION_SIMILARITY:
            return row
    mute_id = uuid.uuid4().hex[:12]
    with storage.connect() as db:
        db.execute("INSERT INTO dossier_mutes (id,scope,owner_id,text,meeting_id,created_at) VALUES (?,?,?,?,?,?)",
                   (mute_id, scope, owner_id, body, meeting_id[:80], _now()))
    return storage.one("SELECT * FROM dossier_mutes WHERE id=?", (mute_id,))


def unmute_question(mute_id: str) -> dict:
    row = storage.one("SELECT * FROM dossier_mutes WHERE id=?", (mute_id,))
    if not row:
        raise KeyError("Вопрос не найден")
    with storage.connect() as db:
        db.execute("DELETE FROM dossier_mutes WHERE id=?", (mute_id,))
    return row


def hanging_questions(meetings: list[knowledge.Meeting], decisions: list[dict] | None = None) -> list[dict]:
    """Вопросы, повторяющиеся на нескольких встречах и всё ещё открытые. `meetings` — записи человека или проекта в любом порядке."""
    chrono = sorted(meetings, key=_order)
    if decisions is None:
        wanted = {m.id for m in chrono}
        decisions = [r for r in storage.rows("SELECT meeting_id,text FROM decisions WHERE status!='rejected'") if r["meeting_id"] in wanted]
    decided: dict[str, list[frozenset[str]]] = {}
    for d in decisions:
        decided.setdefault(d["meeting_id"], []).append(series.tokens(d["text"]))

    clusters: list[dict] = []
    for index, m in enumerate(chrono):
        for fact in knowledge.fact_items(m, "Открытые вопросы"):
            key = series.tokens(fact["text"])
            if not key:
                continue
            best, best_score = None, 0.0
            for cluster in clusters:
                score = max(series.similarity(key, other) for other in cluster["keys"])
                if score >= series.QUESTION_SIMILARITY and score > best_score:
                    best, best_score = cluster, score
            occurrence = {"meetingId": m.id, "meetingTitle": m.title, "date": services.meeting_date(m), "sec": fact["sec"], "index": index}
            if best is None:
                clusters.append({"keys": [key], "text": fact["text"], "occurrences": [occurrence]})
                continue
            best["keys"].append(key)
            best["text"] = fact["text"]                      # формулировка последней встречи — самая свежая
            if best["occurrences"][-1]["meetingId"] == m.id:
                continue                                      # в одной записи один и тот же вопрос дважды не считаем
            best["occurrences"].append(occurrence)

    out = []
    last_index = len(chrono) - 1
    for cluster in clusters:
        occurrences = cluster["occurrences"]
        if len(occurrences) < HANGING_FROM:
            continue
        last = occurrences[-1]
        if last["index"] < last_index - (HANGING_WINDOW - 1):
            continue                                          # давно не поднимался: считаем снятым с повестки
        answered = any(series.similarity(key, token) >= ANSWERED_SIMILARITY
                       for later in chrono[last["index"] + 1:] for token in decided.get(later.id, []) for key in cluster["keys"])
        if answered:
            continue
        out.append({
            "text": cluster["text"], "count": len(occurrences), "first": occurrences[0]["date"], "last": last["date"],
            "occurrences": [{k: v for k, v in o.items() if k != "index"} for o in occurrences],
            "keys": cluster["keys"],
        })
    return out


def hanging_view(scope: str, owner_id: str, meetings: list[knowledge.Meeting]) -> dict:
    muted_rows = _mutes(scope, owner_id)
    muted_keys = [(r, series.tokens(r["text"])) for r in muted_rows]
    shown, hidden = [], []
    for q in hanging_questions(meetings):
        keys = q.pop("keys")
        if any(series.similarity(k, mk) >= series.QUESTION_SIMILARITY for _row, mk in muted_keys for k in keys):
            hidden.append(q)
        else:
            shown.append(q)
    shown.sort(key=lambda q: (-q["count"], q["last"]))
    return {"items": shown[:HANGING_LIMIT], "total": len(shown), "muted": [{"id": r["id"], "text": r["text"]} for r in muted_rows],
            "from": HANGING_FROM}


# --------------------------------------------------------------------------- что изменилось

def changes(meetings: list[knowledge.Meeting]) -> list[dict]:
    recent = sorted(meetings, key=_order, reverse=True)[:CHANGES_LIMIT]
    if not recent:
        return []
    ids = [m.id for m in recent]
    marks = ",".join("?" * len(ids))
    tasks = Counter(r["meeting_id"] for r in storage.rows(f"SELECT meeting_id FROM tasks WHERE status!='rejected' AND meeting_id IN ({marks})", tuple(ids)))
    decisions = Counter(r["meeting_id"] for r in storage.rows(f"SELECT meeting_id FROM decisions WHERE status!='rejected' AND meeting_id IN ({marks})", tuple(ids)))
    out = []
    for m in recent:
        out.append({"meetingId": m.id, "meetingTitle": m.title, "date": services.meeting_date(m), "decisions": decisions[m.id], "tasks": tasks[m.id],
                    "questions": len(knowledge.fact_items(m, "Открытые вопросы")), "risks": len(knowledge.fact_items(m, "Риски"))})
    return out


# --------------------------------------------------------------------------- о чём разговор

def _material(meetings: list[knowledge.Meeting]) -> list[dict]:
    items = []
    for m in sorted(meetings, key=_order, reverse=True):
        bullets = [b for b in knowledge.summary_bullets(m, GIST_BULLETS) if b]
        if bullets:
            items.append({"id": m.id, "title": m.title, "date": services.meeting_date(m), "bullets": bullets})
        if len(items) >= GIST_MEETINGS:
            break
    return items


def _signature(material: list[dict]) -> str:
    return hashlib.sha1(json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def _sources(material: list[dict]) -> list[dict]:
    return [{"meetingId": i["id"], "meetingTitle": i["title"], "date": i["date"]} for i in material]


def structural_gist(material: list[dict]) -> str:
    """Без модели: первое содержательное предложение каждой из последних записей, от новой к старой."""
    parts = []
    for item in material[:3]:
        line = item["bullets"][0].rstrip(".;:, ")
        parts.append(f"{line} ({digest.short(item['date']) if item['date'] else item['title']})")
    text = "; ".join(parts)
    return (text[:GIST_MAX - 1].rsplit(" ", 1)[0] + "…") if len(text) > GIST_MAX else text


_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b[А-ЯЁA-Z][а-яёa-z]{3,}")


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]+", text.casefold().replace("ё", "е")) if len(w) >= 4}


def _trusted(answer: str, source: str) -> bool:
    """Ответ модели годится, если в нём нет цифр и заглавных слов (имён), которых нет в исходных пунктах."""
    from .artifacts import _numbers
    if not answer or len(answer) > GIST_MAX + 80 or "\n" in answer or not _numbers(answer) <= _numbers(source):
        return False
    known = _stems(source)
    return all(w.casefold().replace("ё", "е")[:5] in known for w in _NAME.findall(answer))


def model_gist(material: list[dict], who: str) -> str | None:
    lines = []
    for item in material:
        lines.append(f"{item['date'] or ''} «{item['title']}»:")
        lines.extend("- " + b for b in item["bullets"])
    source = "\n".join(lines)
    prompt = (f"Ты помощник по деловым записям. Ниже краткие содержания последних встреч ({who}). Напиши одним-двумя короткими "
              "предложениями, о чём обычно идёт разговор и что сейчас в центре. Пиши только по этим пунктам: ни новых цифр, ни имён, "
              "ни дат, которых в них нет. Без списков и вступлений, по-русски.\n\n" + source + "\n\nТЕКСТ:\n")
    try:
        answer = digest.generate(prompt, 180).strip()
    except OSError:
        return None
    return answer if _trusted(answer, source) else None


def _stored_gist(scope: str, owner_id: str) -> dict | None:
    return storage.one("SELECT * FROM dossier_gists WHERE scope=? AND owner_id=?", (scope, owner_id))


def gist_view(scope: str, owner_id: str, meetings: list[knowledge.Meeting]) -> dict:
    """Абзац для досье: сохранённый, если записи не менялись; иначе структурный, помеченный как устаревший. Модель не вызывается."""
    material = _material(meetings)
    if not material:
        return {"text": "", "engine": "", "stale": False, "sources": [], "updatedAt": "", "busy": _busy(scope, owner_id)}
    signature = _signature(material)
    stored = _stored_gist(scope, owner_id)
    if stored and stored["signature"] == signature:
        return {"text": stored["text"], "engine": stored["engine"], "stale": False, "sources": _sources(material),
                "updatedAt": stored["updated_at"], "busy": _busy(scope, owner_id)}
    return {"text": structural_gist(material), "engine": "structure", "stale": bool(stored), "sources": _sources(material),
            "updatedAt": stored["updated_at"] if stored else "", "busy": _busy(scope, owner_id)}


def refresh_gist(scope: str, owner_id: str, meetings: list[knowledge.Meeting], who: str, *, use_model: bool = True) -> dict:
    """Пересчитывает абзац и сохраняет его. Модель недоступна или ответила сомнительно — сохраняется структурный вариант."""
    material = _material(meetings)
    if not material:
        with storage.connect() as db:
            db.execute("DELETE FROM dossier_gists WHERE scope=? AND owner_id=?", (scope, owner_id))
        return {"text": "", "engine": ""}
    text = model_gist(material, who) if use_model else None
    engine = "model" if text else "structure"
    text = text or structural_gist(material)
    with storage.connect() as db:
        db.execute("INSERT INTO dossier_gists (scope,owner_id,text,engine,signature,sources,updated_at) VALUES (?,?,?,?,?,?,?) "
                   "ON CONFLICT(scope,owner_id) DO UPDATE SET text=excluded.text,engine=excluded.engine,signature=excluded.signature,"
                   "sources=excluded.sources,updated_at=excluded.updated_at",
                   (scope, owner_id, text, engine, _signature(material), json.dumps(_sources(material), ensure_ascii=False), _now()))
    return {"text": text, "engine": engine}


def forget_owner(scope: str, owner_id: str) -> None:
    with storage.connect() as db:
        for table in ("dossier_pins", "dossier_mutes", "dossier_gists"):
            db.execute(f"DELETE FROM {table} WHERE scope=? AND owner_id=?", (scope, owner_id))


# --------------------------------------------------------------------------- обновление по событию

def _busy(scope: str, owner_id: str) -> bool:
    with _state_lock:
        return bool(_state.get((scope, owner_id), {}).get("busy"))


def meetings_of(core, scope: str, owner_id: str) -> tuple[list[knowledge.Meeting], str]:
    """(записи человека или проекта, от новых к старым; название для запроса). Пусто, если такого человека или проекта нет."""
    live = core.live_meetings()
    if scope == "person":
        row = storage.one("SELECT name FROM people WHERE id=?", (owner_id,))
        if not row:
            return [], ""
        people_map = storage.people_by_meeting()
        return [m for m in live if any(p["id"] == owner_id for p in people_map.get(m.id, []))], row["name"]
    found = next((p for p in core.projects() if p["id"] == owner_id), None)
    if not found:
        return [], ""
    return [m for m in live if m.project_id == owner_id or (not m.project_id and m.project == found["title"])], found["title"]


def _run(core, owners: list[tuple[str, str]], actor: str, use_model: bool) -> None:
    done = 0
    try:
        for scope, owner_id in owners:
            try:
                meetings, name = meetings_of(core, scope, owner_id)
                if not name:
                    continue
                refresh_gist(scope, owner_id, meetings, ("человек: " if scope == "person" else "проект: ") + name, use_model=use_model)
                done += 1
            except Exception:  # noqa: BLE001 — сбой одного досье не должен остановить остальные
                continue
    finally:
        with _state_lock:
            for key in owners:
                _state.pop(key, None)
        if done:
            core.data_version += 1
            audit.record(actor, "dossier.refresh", target="system", detail=f"досье: {done}")


def refresh_async(core, owners: list[tuple[str, str]], *, actor: str = "system", use_model: bool = True) -> bool:
    """Запускает пересчёт в фоне; True, если запущен (досье, которые уже считаются, пропускаются)."""
    with _state_lock:
        fresh = [key for key in owners if not _state.get(key, {}).get("busy")]
        for key in fresh:
            _state[key] = {"busy": True}
    if not fresh:
        return False
    threading.Thread(target=_run, args=(core, fresh, actor, use_model), daemon=True, name="dossier-refresh").start()
    return True


def owners_of_meeting(core, meeting_id: str) -> list[tuple[str, str]]:
    m = core.by_id.get(meeting_id)
    if not m:
        return []
    owners = [("person", p["id"]) for p in storage.people_by_meeting().get(meeting_id, [])]
    if m.project_id:
        owners.append(("project", m.project_id))
    else:
        found = next((p for p in core.projects() if p["title"] == m.project), None) if m.project else None
        if found:
            owners.append(("project", found["id"]))
    return owners


def refresh_for_meeting_async(core, meeting_id: str, *, use_model: bool = True) -> bool:
    """Запись обработана: пересчитать «о чём разговор» у её участников и проекта."""
    owners = owners_of_meeting(core, meeting_id)
    return refresh_async(core, owners, use_model=use_model) if owners else False


# --------------------------------------------------------------------------- сборка для страницы

def extras(core, scope: str, owner_id: str, meetings: list[knowledge.Meeting]) -> dict:
    return {
        "pins": pins(core, scope, owner_id),
        "hanging": hanging_view(scope, owner_id, meetings),
        "gist": gist_view(scope, owner_id, meetings),
        "changes": changes(meetings),
    }
