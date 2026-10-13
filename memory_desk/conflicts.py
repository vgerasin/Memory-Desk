"""Расхождения между решениями: новое решение, похожее на старое в том же проекте, может его заменять.

Кандидатов находим без модели (по сходству формулировок), локальная модель по кнопке даёт совет по паре, а решение
(«отметить старое заменённым» или «не связаны») всегда принимает человек."""
from __future__ import annotations

import json

from . import config, storage
from .series import similarity, tokens

MIN_SIMILARITY = 0.34   # сходство формулировок (доля общих основ слов)
MIN_SHARED = 2          # и не меньше двух общих слов, чтобы не сводить «Перейти на X» с «Перейти на Y»
LIMIT = 30
SETTING = "dismissed_conflicts"


def _dismissed() -> set[str]:
    try:
        return set(json.loads(storage.get_setting(SETTING, "[]")))
    except ValueError:
        return set()


def pair_key(old_id: str, new_id: str) -> str:
    return f"{old_id}|{new_id}"


def dismiss(old_id: str, new_id: str) -> None:
    storage.set_setting(SETTING, json.dumps(sorted(_dismissed() | {pair_key(old_id, new_id)})))


def candidates(dto) -> list[dict]:
    """Пары (старое, новое) по проектам. `dto(row, title, date)` собирает представление решения."""
    rows = storage.rows(
        "SELECT d.*, m.title mt, m.meeting_date md, m.recorded_at ra, m.project_id pid, m.project_title ptitle "
        "FROM decisions d JOIN meetings m ON m.id=d.meeting_id "
        "WHERE d.status!='rejected' AND m.archived=0 AND COALESCE(m.project_id,'')!='' ORDER BY m.recorded_at, d.rowid")
    dismissed = _dismissed()
    by_project: dict[str, list[dict]] = {}
    for row in rows:
        by_project.setdefault(row["pid"], []).append({**row, "tokens": tokens(row["text"])})
    found = []
    for items in by_project.values():
        for i, old in enumerate(items):
            if old["replaced_by"]:
                continue
            best = None
            for new in items[i + 1:]:
                if new["meeting_id"] == old["meeting_id"] or (new["ra"] or "") <= (old["ra"] or ""):
                    continue
                if new["replaced_by"] == old["id"] or pair_key(old["id"], new["id"]) in dismissed:
                    continue
                shared = len(old["tokens"] & new["tokens"])
                score = similarity(old["tokens"], new["tokens"])
                if shared >= MIN_SHARED and score >= MIN_SIMILARITY:
                    best = (score, new)  # перезаписываем: из подходящих берём самое позднее решение
            if best:
                found.append({"key": pair_key(old["id"], best[1]["id"]), "similarity": round(best[0], 2), "project": old["ptitle"],
                              "old": dto(old, old["mt"], old["md"]), "new": dto(best[1], best[1]["mt"], best[1]["md"])})
    found.sort(key=lambda p: (-p["similarity"], p["key"]))
    return found[:LIMIT]


def ask_model(old: dict, new: dict) -> dict:
    """Совет локальной модели по паре: заменяет, противоречит, согласуется или не связано. Недоступность модели — RuntimeError."""
    from . import llm
    prompt = (
        "Ты помощник по архиву рабочих встреч. Есть два решения из одного проекта. Определи, как новое решение соотносится со старым.\n"
        f"Старое решение ({old['date']}): {old['text']}\n"
        f"Новое решение ({new['date']}): {new['text']}\n\n"
        'Верни только JSON: {"relation": "replaces|contradicts|consistent|unrelated", "reason": "одно короткое предложение по-русски"}.\n'
        "replaces — новое отменяет или заменяет старое; contradicts — они противоречат, но замена не очевидна; "
        "consistent — они согласуются или уточняют друг друга; unrelated — это разные вопросы. Не придумывай фактов.")
    data = llm.ollama_json(prompt, model=config.OLLAMA_MODEL, url=config.OLLAMA_URL, num_predict=300, attempts=2, timeout=120)
    relation = str(data.get("relation", "")).strip().lower()
    if relation not in {"replaces", "contradicts", "consistent", "unrelated"}:
        raise RuntimeError("Модель ответила непонятно")
    return {"relation": relation, "reason": " ".join(str(data.get("reason", "")).split())[:300]}
