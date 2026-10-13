"""Пересборка заметки готовой записи по другому шаблону.

Стенограмма, метаданные (проект, тип, участники), заголовок и карточка записи остаются как есть. Заново
составляются разделы между карточкой и «Стенограммой». Задачи и решения, которые пользователь уже подтвердил,
закрыл или отклонил, не теряются: они переходят в новую заметку, а черновики заменяются новыми. Прежняя заметка
сохраняется в `<данные>/note-backups/`.
"""
from __future__ import annotations

import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import config, knowledge, storage, templates

STATE: dict[str, dict] = {}
_lock = threading.Lock()


class RebuildError(RuntimeError):
    """Пересобрать заметку не удалось; текст понятен пользователю."""


def _worker():
    try:
        import meetings_process
    except ImportError:
        sys.path.insert(0, str(config.PROJECT_ROOT))
        import meetings_process
    return meetings_process


def backup_dir() -> Path:
    return config.DATA_DIR / "note-backups"


def _merged(table: str, meeting_id: str, facts: list[dict]) -> tuple[list[dict], list[dict], int]:
    """(строки заметки, новые черновики для базы, число сохранённых подтверждённых записей)."""
    plain = knowledge._plain
    kept = [r for r in storage.rows(f"SELECT * FROM {table} WHERE meeting_id=?", (meeting_id,)) if r["status"] != "draft"]
    by_text = {plain(r["text"]): r for r in kept}
    used: set[str] = set()
    lines: list[dict] = []
    drafts: list[dict] = []
    for fact in facts:
        text = str(fact.get("task") if table == "tasks" else fact.get("text") or "").strip()
        if not text:
            continue
        key = plain(text)
        row = by_text.get(key)
        if row is not None:
            if key in used:
                continue
            used.add(key)
            lines.append({"text": row["text"], "owner": row.get("owner"), "deadline": row.get("deadline"),
                          "status": row["status"], "evidence_time": row.get("evidence_time") or fact.get("evidence_time")})
            continue
        item = {"text": text, "owner": fact.get("owner"), "deadline": fact.get("deadline"),
                "evidence_time": fact.get("evidence_time"), "status": "draft", "key": key}
        lines.append(item)
        drafts.append(item)
    for key, row in by_text.items():
        if key not in used:
            lines.append({"text": row["text"], "owner": row.get("owner"), "deadline": row.get("deadline"),
                          "status": row["status"], "evidence_time": row.get("evidence_time")})
    return lines, drafts, len(kept)


def _replace_section(text: str, heading: str, body: str) -> str:
    pattern = rf"(?ms)(^##[ \t]+{re.escape(heading)}[ \t]*\n\n).*?(?=^##[ \t]+|\Z)"
    return re.sub(pattern, lambda m: m.group(1) + body.rstrip() + "\n\n", text, count=1)


def _swap_registry(meeting_id: str, table: str, drafts: list[dict]) -> None:
    now = storage.now_iso()
    with storage.connect() as db:
        if table == "decisions":
            db.execute("DELETE FROM decisions WHERE meeting_id=? AND status='draft' AND COALESCE(replaced_by,'')='' "
                       "AND id NOT IN (SELECT replaced_by FROM decisions WHERE COALESCE(replaced_by,'')!='')", (meeting_id,))
        else:
            db.execute("DELETE FROM tasks WHERE meeting_id=? AND status='draft'", (meeting_id,))
        for item in drafts:
            item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{meeting_id}:{table}:{item['key']}"))
            owner = "" if str(item.get("owner") or "").casefold() in {"не указан", "не указано"} else str(item.get("owner") or "")
            if table == "tasks":
                deadline = "" if str(item.get("deadline") or "").casefold() in {"не указан", "не указано"} else str(item.get("deadline") or "")
                db.execute("INSERT OR IGNORE INTO tasks (id,meeting_id,text,owner,deadline,evidence_time,status,updated_at)"
                           " VALUES (?,?,?,?,?,?,?,?)",
                           (item_id, meeting_id, item["text"], owner, deadline, item.get("evidence_time") or "", "draft", now))
            else:
                db.execute("INSERT OR IGNORE INTO decisions (id,meeting_id,text,owner,evidence_time,status,updated_at)"
                           " VALUES (?,?,?,?,?,?,?)",
                           (item_id, meeting_id, item["text"], "", item.get("evidence_time") or "", "draft", now))


def build_text(meeting: knowledge.Meeting, template: templates.Template, data: dict, used: templates.Template,
               fallback: str) -> tuple[str, list[dict], list[dict], int, int]:
    """Новый текст заметки. Возвращает (текст, черновики задач, черновики решений, сохранено задач, сохранено решений)."""
    mp = _worker()
    text = meeting.path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    if not text.startswith("---\n") or end == -1:
        raise RebuildError("В заметке нет метаданных: пересобрать её нельзя")
    front, body = text[:end + 5], text[end + 5:]
    first = re.search(r"(?m)^##[ \t]+", body)
    if not first:
        raise RebuildError("В заметке нет разделов")
    preamble = re.sub(r"(?ms)^> \[!note\] Шаблон «.*?» не получился\n(?:>.*\n)*", "", body[:first.start()]).rstrip() + "\n"
    if used.id != template.id:
        preamble += (f"\n> [!note] Шаблон «{template.name}» не получился\n"
                     f"> Заметка составлена по стандартному шаблону «{used.name}». Причина: {fallback or 'ответ модели не подошёл'}.\n")
    tail = re.search(r"(?ms)^##[ \t]+(?:Полная )?[Сс]тенограмма[ \t]*\n.*\Z", body)
    transcript_block = ""
    if tail:
        kept = []
        for line in tail.group(0).split("\n"):
            if kept and line.strip() and not line.startswith(">") and not kept[0].startswith("## Полная"):
                break
            kept.append(line)
        transcript_block = "\n".join(kept).rstrip() + "\n"
        if kept and kept[0].startswith("## Полная"):
            transcript_block = tail.group(0).rstrip() + "\n"
    sections = mp.render_sections(data, used)
    task_lines, task_drafts, kept_tasks = _merged("tasks", meeting.id, data.get("tasks") or [])
    decision_lines, decision_drafts, kept_decisions = _merged("decisions", meeting.id, data.get("decisions") or [])
    if used.has("tasks"):
        sections = _replace_section(sections + "\n", "Задачи", "\n".join(
            knowledge.render_item_line("tasks", i) for i in task_lines) or "Задачи не зафиксированы.")
    if used.has("decisions"):
        sections = _replace_section(sections + "\n", knowledge.DECISION_SECTION, "\n".join(
            knowledge.render_item_line("decisions", i) for i in decision_lines) or "Не выявлено.")
    signature = f"\n_{used.signature}_\n" if used.signature else ""
    from . import views
    kept_views = views.sections_text(body)  # «Взгляды» не зависят от шаблона и переживают пересборку
    views_block = f"{kept_views.strip()}\n\n" if kept_views.strip() else ""
    new_body = f"{preamble}\n{sections.strip()}\n\n{views_block}{transcript_block}{signature}"
    # поле template в метаданных
    line = f'template: "{used.id}"'
    if re.search(r"(?m)^template:.*$", front):
        front = re.sub(r"(?m)^template:.*$", lambda _m: line, front, count=1)
    else:
        front = front[:front.rfind("\n---\n")] + "\n" + line + "\n---\n"
    return front + new_body, (task_drafts if used.has("tasks") else []), \
        (decision_drafts if used.has("decisions") else []), kept_tasks, kept_decisions


def rebuild(core, meeting_id: str, template_id: str) -> dict:
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise RebuildError("Запись не найдена")
    template = templates.get(template_id)
    if template.id != template_id:
        raise RebuildError("Такого шаблона нет")
    transcript = knowledge.meeting_transcript(meeting).strip()
    if len(transcript) < 20:
        raise RebuildError("У записи нет стенограммы: пересобирать заметку не из чего")
    mp = _worker()
    data, used, fallback = mp.analyze_with_template(transcript, template)
    text, task_drafts, decision_drafts, kept_tasks, kept_decisions = build_text(meeting, template, data, used, fallback)
    backup_dir().mkdir(parents=True, exist_ok=True)
    backup = backup_dir() / f"{meeting.path.stem}.{datetime.now():%Y%m%d-%H%M%S}.md"
    shutil.copy2(meeting.path, backup)
    temp = meeting.path.with_name(f".{meeting.path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(meeting.path)
    # Черновики прежнего разбора больше не актуальны, даже если у нового шаблона нет таких разделов;
    # подтверждённые и закрытые записи остаются в реестре.
    _swap_registry(meeting.id, "tasks", task_drafts)
    _swap_registry(meeting.id, "decisions", decision_drafts)
    core.reload(force=True)
    return {"templateId": used.id, "requestedId": template.id, "fallback": fallback if used.id != template.id else "",
            "keptTasks": kept_tasks, "keptDecisions": kept_decisions, "backup": backup.name}


# --------------------------------------------------------------------------- фоновый запуск

def start(core, meeting_id: str, template_id: str) -> dict:
    """Запускает пересборку в фоне. Одновременно идёт только одна."""
    with _lock:
        if any(s["state"] == "running" for s in STATE.values()):
            raise RebuildError("Другая заметка уже пересобирается: дождитесь окончания")
        if meeting_id not in core.by_id:
            raise RebuildError("Запись не найдена")
        if templates.get(template_id).id != template_id:
            raise RebuildError("Такого шаблона нет")
        STATE[meeting_id] = {"state": "running", "templateId": template_id, "startedAt": time.time(), "message": "Составляю заметку"}

    def work() -> None:
        try:
            result = rebuild(core, meeting_id, template_id)
            STATE[meeting_id] = {"state": "done", "finishedAt": time.time(), **result}
        except Exception as exc:  # noqa: BLE001 — причина должна дойти до интерфейса
            STATE[meeting_id] = {"state": "error", "finishedAt": time.time(), "templateId": template_id, "message": str(exc)}

    threading.Thread(target=work, name="rebuild-note", daemon=True).start()
    return STATE[meeting_id]


def status(meeting_id: str) -> dict:
    return STATE.get(meeting_id, {"state": "idle"})
