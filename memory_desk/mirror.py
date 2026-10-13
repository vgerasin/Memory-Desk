"""Сводные файлы для Obsidian: «Сводки/Задачи.md» и «Сводки/Решения.md».

Рабочее состояние задач и решений (статус, срок, ответственный, замены) живёт в базе кабинета. Чтобы граф, поиск и плагины
Obsidian видели его целиком, оно выгружается в два файла, сгруппированные по проектам; в каждой строке ссылка на запись и
таймкод. Файлы создаются автоматически и перезаписываются только когда изменилось содержимое: править их руками не нужно
(правки не сохранятся). Заметки записей при этом не затрагиваются."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import knowledge, storage

FOLDER = "Сводки"
NO_PROJECT = "Без проекта"
DONE_DAYS = 30
NOTICE = "> Файл создаётся Memory Desk автоматически из базы кабинета. Меняйте задачи и решения в кабинете: правки здесь не сохранятся."


def folder() -> Path:
    return knowledge.WORKSPACE_DIR / FOLDER


def _link(row: dict) -> str:
    rel = knowledge.note_relative_path(row["path"]).removesuffix(".md")
    title = " ".join(str(row["title"]).split()).replace("|", "/").replace("[", "(").replace("]", ")")
    day = row.get("meeting_date") or ""
    return f"[[{rel}|{title}]]" + (f", {day}" if day else "")


def _stamp(row: dict) -> str:
    t = row.get("evidence_time") or ""
    return f" · `⏱ {t}`" if re.fullmatch(r"\d{2}:\d{2}:\d{2}", t) else ""


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


def _group(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("project_title") or NO_PROJECT, []).append(row)
    return dict(sorted(groups.items(), key=lambda kv: (kv[0] == NO_PROJECT, kv[0].casefold())))


def _head(title: str, count: int) -> list[str]:
    return ["---", "type: mirror", f"title: {title}", f"items: {count}", "---", "", f"# {title}", "", NOTICE]


def tasks_text() -> str:
    cutoff = (datetime.now() - timedelta(days=DONE_DAYS)).isoformat(timespec="seconds")
    rows = storage.rows(
        "SELECT t.*, m.title, m.path, m.meeting_date, m.project_title FROM tasks t JOIN meetings m ON m.id=t.meeting_id "
        "WHERE COALESCE(m.archived,0)=0 AND (t.status IN ('draft','open') OR (t.status='done' AND t.updated_at>=?)) "
        "ORDER BY m.meeting_date DESC, t.rowid", (cutoff,))
    lines = _head("Задачи", len(rows))
    if not rows:
        return "\n".join(lines + ["", "Задач пока нет."]) + "\n"
    for project, items in _group(rows).items():
        lines += ["", f"## {project}"]
        for title, states, mark in (("В работе", ("open",), " "), ("Ждут подтверждения", ("draft",), " "), (f"Выполнено за {DONE_DAYS} дней", ("done",), "x")):
            part = sorted((i for i in items if i["status"] in states), key=lambda i: ((i.get("deadline") or "9999")[:10], i["text"]))
            if not part:
                continue
            lines += ["", f"### {title}", ""]
            for i in part:
                owner = _clean(i.get("owner"))
                owner = "" if owner.casefold() in {"не указано", "не указан"} else owner
                due = _clean(i.get("deadline"))
                due = "" if due.casefold() in {"", "не указан", "не указано"} else due
                bits = [f"**ответственный:** {owner}"] if owner else []
                if due:
                    bits.append(f"**срок:** {due}")
                tail = " — " + "; ".join(bits) if bits else ""
                lines.append(f"- [{mark}] {_clean(i['text'])}{tail} · {_link(i)}{_stamp(i)}")
    return "\n".join(lines) + "\n"


def decisions_text() -> str:
    rows = storage.rows(
        "SELECT d.*, m.title, m.path, m.meeting_date, m.project_title FROM decisions d JOIN meetings m ON m.id=d.meeting_id "
        "WHERE COALESCE(m.archived,0)=0 AND d.status!='rejected' ORDER BY m.meeting_date DESC, d.rowid")
    texts = {r["id"]: r["text"] for r in rows}
    lines = _head("Решения", len(rows))
    if not rows:
        return "\n".join(lines + ["", "Решений пока нет."]) + "\n"
    for project, items in _group(rows).items():
        lines += ["", f"## {project}"]
        active = [i for i in items if not i.get("replaced_by")]
        replaced = [i for i in items if i.get("replaced_by")]
        if active:
            lines += ["", "### Действующие", ""]
            for i in active:
                mark = " _(не подтверждено)_" if i["status"] == "draft" else ""
                lines.append(f"- {_clean(i['text'])}{mark} · {_link(i)}{_stamp(i)}")
        if replaced:
            lines += ["", "### Заменены", ""]
            for i in replaced:
                new = texts.get(i["replaced_by"])
                lines.append(f"- ~~{_clean(i['text'])}~~ · {_link(i)}{_stamp(i)}" + (f" → {_clean(new)}" if new else ""))
    return "\n".join(lines) + "\n"


def _write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
    return True


def refresh() -> dict:
    """Обновляет оба файла. Возвращает, какие были переписаны."""
    storage.initialize()
    out = {}
    for name, text in (("Задачи.md", tasks_text()), ("Решения.md", decisions_text())):
        path = folder() / name
        out[name] = _write_if_changed(path, text)
    return {"folder": FOLDER, "written": [n for n, changed in out.items() if changed], "files": list(out)}


def fingerprint() -> str:
    """Отпечаток содержимого: по нему видно, что выгружать нечего."""
    return hashlib.sha1((tasks_text() + decisions_text()).encode("utf-8")).hexdigest()
