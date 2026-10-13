"""Заметки Obsidian, стенограммы, семантический поиск и ответы по архиву."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from . import config, diarize, storage

COLLECTION = "meetings"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_SIZE = 384
INDEX_VERSION = 2  # меняется при изменении состава полей индекса — запускает пересборку
MIN_ANSWER_SCORE = 0.25  # ниже — подтверждающих фрагментов в архиве нет
RU_MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
             "сентября", "октября", "ноября", "декабря")
OVERVIEW_SECTIONS = ("Краткое резюме", "Кратко", "Основные темы", "Предполагаемые решения и договорённости",
                     "Задачи", "Риски", "Следующие шаги")
SUMMARY_SECTIONS = ("Кратко", "Краткое резюме")
DECISION_SECTION = "Предполагаемые решения и договорённости"
DEFAULT_RECORD_TYPES = (
    ("meeting", "Встреча"), ("offline_meeting", "Очная встреча"), ("phone_call", "Телефонный звонок"),
    ("zoom", "Zoom"), ("thought", "Голосовая мысль"), ("interview", "Интервью"),
)

# --------------------------------------------------------------------------- пути

VAULT = WORKSPACE_DIR = PROJECTS_DIR = RECORD_TYPES_DIR = AUDIO_ROOT = ARCHIVE_ROOT = Path(".")
OBSIDIAN_VAULT_ROOT = Path(".")
OBSIDIAN_VAULT_NAME = ""


def _obsidian_root(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if (candidate / ".obsidian").exists():
            return candidate
    return path.parent


def configure_paths(values: dict) -> None:
    global VAULT, WORKSPACE_DIR, PROJECTS_DIR, RECORD_TYPES_DIR, AUDIO_ROOT, ARCHIVE_ROOT
    global OBSIDIAN_VAULT_ROOT, OBSIDIAN_VAULT_NAME
    VAULT = Path(values["obsidian_dir"]).expanduser()
    WORKSPACE_DIR = VAULT.parent
    PROJECTS_DIR = WORKSPACE_DIR / "Проекты"
    RECORD_TYPES_DIR = WORKSPACE_DIR / "Справочники/Типы записей"
    AUDIO_ROOT = Path(values["recordings_dir"]).expanduser()
    ARCHIVE_ROOT = Path(values["archive_dir"]).expanduser()
    OBSIDIAN_VAULT_ROOT = _obsidian_root(VAULT)
    OBSIDIAN_VAULT_NAME = OBSIDIAN_VAULT_ROOT.name


configure_paths(config.load_config())


# --------------------------------------------------------------------------- frontmatter и разделы

def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw, body = text[4:end], text[end + 5:]
    meta: dict[str, object] = {}
    current_list = None
    for line in raw.splitlines():
        if line.startswith("  - ") and current_list:
            meta.setdefault(current_list, []).append(line[4:].strip().strip('"'))
            continue
        match = re.match(r"^([\w-]+):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        if value:
            meta[key] = value.strip().strip('"')
            current_list = None
        else:
            meta[key] = []
            current_list = key
    return meta, body


def parse_note_sections(content: str) -> dict[str, str]:
    """Полный текст каждого раздела `## Заголовок` заметки."""
    result: dict[str, str] = {}
    for part in re.split(r"(?m)^##\s+", content)[1:]:
        heading, _, body = part.partition("\n")
        result[heading.strip()] = body.strip()
    return result


def to_seconds(stamp: str) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", stamp or "")
    if match:
        h, m, s = map(int, match.groups())
        return h * 3600 + m * 60 + s
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", stamp or "")
    if match:
        m, s = map(int, match.groups())
        return m * 60 + s
    return None


def format_clock(seconds: int | float | None) -> str:
    if seconds is None:
        return ""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# --------------------------------------------------------------------------- проекты и типы записей

def load_projects() -> list[dict]:
    projects = []
    if not PROJECTS_DIR.exists():
        return projects
    for path in sorted(PROJECTS_DIR.glob("*.md")):
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if str(meta.get("type", "")) != "project":
            continue
        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        sections = parse_note_sections(body)
        goal = sections.get("Цели") or sections.get("О проекте") or ""
        goal = " ".join(line.lstrip("-• ").strip() for line in goal.splitlines() if line.strip())
        projects.append({
            "id": str(meta.get("project_id") or path.stem),
            "title": str(meta.get("title") or (title_match.group(1) if title_match else path.stem)),
            "status": str(meta.get("status") or "active"),
            "path": str(path),
            "goal": goal,
        })
    return projects


def create_project(title: str) -> dict:
    title = " ".join(title.split())
    if not title:
        raise ValueError("Название проекта не может быть пустым")
    if re.search(r"[\\/:*?\"<>|]", title):
        raise ValueError("Название содержит недопустимые символы")
    if len(title) > 80:
        raise ValueError("Название проекта слишком длинное: не больше 80 знаков")
    if not re.search(r"\w", title):
        raise ValueError("В названии проекта должна быть хотя бы одна буква или цифра")
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project_id = re.sub(r"[^\wа-яё-]+", "-", title.casefold(), flags=re.IGNORECASE).strip("-") or "project"
    path = PROJECTS_DIR / f"{title}.md"
    if path.exists():
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if str(meta.get("type", "")) == "project":
            return {"id": str(meta.get("project_id") or project_id), "title": str(meta.get("title") or title),
                    "status": str(meta.get("status") or "active"), "path": str(path), "created": False}
        raise ValueError("Файл с таким названием уже существует, но не является проектом")
    today = datetime.now().strftime("%Y-%m-%d")
    path.write_text(
        f"---\ntype: project\nproject_id: \"{project_id}\"\ntitle: \"{title}\"\nstatus: active\n"
        f"created_at: {today}\n---\n\n# {title}\n\n## О проекте\n\n## Цели\n\n## Связанные записи\n",
        encoding="utf-8",
    )
    return {"id": project_id, "title": title, "status": "active", "path": str(path), "created": True}


PROJECT_STATUSES = ("active", "paused", "done")


def update_project(project_id: str, *, status: str | None = None, goal: str | None = None) -> dict:
    """Статус и цель проекта пишутся в его файл `Проекты/<Название>.md`; остальной текст файла не меняется."""
    project = next((p for p in load_projects() if p["id"] == project_id), None)
    if not project:
        raise KeyError("Проект не найден")
    if status is not None and status not in PROJECT_STATUSES:
        raise ValueError("Статус проекта: active, paused или done")
    path = Path(project["path"])
    text = path.read_text(encoding="utf-8")
    if status is not None:
        if re.search(r"(?m)^status:.*$", text.split("\n---\n", 1)[0]):
            text = re.sub(r"(?m)^status:.*$", f"status: {status}", text, count=1)
        else:
            text = text.replace("\n---\n", f"\nstatus: {status}\n---\n", 1)
    if goal is not None:
        goal = re.sub(r"(?m)^##\s+", "### ", goal.replace("\r\n", "\n").strip())[:2000]
        block = "## Цели\n\n" + (goal + "\n" if goal else "")
        if re.search(r"(?m)^##\s+Цели\s*$", text):
            text = re.sub(r"(?ms)^##\s+Цели\s*$.*?(?=^##\s|\Z)", lambda _m: block + "\n", text, count=1).rstrip() + "\n"
        else:
            text = text.rstrip() + "\n\n" + block
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
    return next(p for p in load_projects() if p["id"] == project_id)


def create_record_type(title: str, type_id: str | None = None) -> dict:
    title = title.strip()
    if not title:
        raise ValueError("Название типа не может быть пустым")
    RECORD_TYPES_DIR.mkdir(parents=True, exist_ok=True)
    resolved = type_id or re.sub(r"[^\wа-яё-]+", "-", title.casefold(), flags=re.IGNORECASE).strip("-") or "record"
    path = RECORD_TYPES_DIR / f"{title}.md"
    created = not path.exists()
    if created:
        path.write_text(
            f'---\ntype: record_type\nrecord_type_id: "{resolved}"\ntitle: "{title}"\nstatus: active\n---\n\n# {title}\n',
            encoding="utf-8")
    return {"id": resolved, "title": title, "path": str(path), "created": created}


def load_record_types() -> list[dict]:
    for type_id, title in DEFAULT_RECORD_TYPES:
        try:
            create_record_type(title, type_id)
        except OSError:
            break
    result = []
    if not RECORD_TYPES_DIR.exists():
        return [{"id": i, "title": t, "path": ""} for i, t in DEFAULT_RECORD_TYPES]
    for path in sorted(RECORD_TYPES_DIR.glob("*.md")):
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if str(meta.get("type", "")) != "record_type":
            continue
        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        result.append({
            "id": str(meta.get("record_type_id") or path.stem),
            "title": str(meta.get("title") or (title_match.group(1) if title_match else path.stem)),
            "path": str(path),
        })
    return result


# --------------------------------------------------------------------------- встречи

@dataclass
class Meeting:
    id: str
    path: Path
    title: str
    date: str
    recorded_at: str
    time: str
    duration: str
    project: str
    project_id: str
    source_type: str
    record_type: str
    source_audio: str
    participants: list[str]
    content: str = field(repr=False)
    template: str = ""


def load_meetings() -> list[Meeting]:
    meetings = []
    if not VAULT.exists():
        return meetings
    for path in sorted(VAULT.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = parse_frontmatter(text)
        if str(meta.get("source", "")).casefold() != "plaud":
            continue
        if str(meta.get("type", "")).casefold() == "transcript":
            continue
        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        recorded_at = str(meta.get("recorded_at", meta.get("date", "")))
        source_audio = str(meta.get("audio_file", ""))
        meeting_id = str(meta.get("meeting_id") or uuid.uuid5(
            uuid.NAMESPACE_URL, f"plaud-local:{recorded_at}|{source_audio or path.stem}"))
        meetings.append(Meeting(
            id=meeting_id, path=path,
            title=title_match.group(1).strip() if title_match else path.stem,
            date=str(meta.get("date", "")), recorded_at=recorded_at, time=str(meta.get("time", "")),
            duration=str(meta.get("duration", "")),
            project=str(meta.get("project") or "Без проекта"), project_id=str(meta.get("project_id", "")),
            source_type=str(meta.get("source_type", "Не указан")),
            record_type=str(meta.get("record_type", meta.get("type", "meeting"))),
            source_audio=source_audio,
            participants=list(meta.get("participants", [])) if isinstance(meta.get("participants"), list) else [],
            content=body, template=str(meta.get("template", "")),
        ))
    return meetings


def update_meeting_fields(meeting: Meeting, fields: dict[str, str]) -> None:
    text = meeting.path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    if not meta:
        raise ValueError("В заметке отсутствуют метаданные")
    end = text.find("\n---\n", 4)
    raw, body = text[4:end], text[end + 5:]
    for key, value in fields.items():
        safe = str(value).replace('"', "'")
        line = f'{key}: "{safe}"'
        if re.search(rf"(?m)^{re.escape(key)}:.*$", raw):
            raw = re.sub(rf"(?m)^{re.escape(key)}:.*$", lambda _m, line=line: line, raw)
        else:
            raw += "\n" + line
    meeting.path.write_text(f"---\n{raw}\n---\n{body}", encoding="utf-8")


def assign_project(meeting: Meeting, project: dict | None) -> None:
    update_meeting_fields(meeting, {
        "project_id": str(project["id"]) if project else "",
        "project": str(project["title"]) if project else "Без проекта",
    })


def assign_record_type(meeting: Meeting, record_type: dict) -> None:
    update_meeting_fields(meeting, {"source_type": str(record_type["title"]), "record_type": str(record_type["id"])})


def update_meeting_text(meeting: Meeting, title: str, summary: str) -> None:
    text = meeting.path.read_text(encoding="utf-8")
    title = " ".join(title.split()) or meeting.title
    text = re.sub(r"(?m)^#\s+.+$", lambda _m: f"# {title}", text, count=1)
    # Заголовки и разделители в тексте превратили бы краткое содержание в новые разделы заметки
    kept = []
    for line in summary.splitlines():
        if re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,}|={3,})\s*", line):
            continue
        kept.append(re.sub(r"^\s{0,3}#{1,6}\s*", "", line))
    summary = "\n".join(kept).strip()
    if summary:
        pattern = r"(?ms)(^##\s+(?:Кратко|Краткое резюме)\s*\n)(.*?)(?=^##\s+|\Z)"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda m: m.group(1) + "\n" + summary + "\n\n", text, count=1)
    meeting.path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- задачи и решения в заметке

ITEM_SECTIONS = {"tasks": "Задачи", "decisions": DECISION_SECTION}


def _plain(text: str) -> str:
    return " ".join(str(text).replace("~~", "").split()).casefold()


def _clean_cell(value: object, empty: str) -> str:
    text = " ".join(str(value or "").replace(";", ",").replace("*", "").replace("`", "'").split())
    return text if text and text.casefold() not in {"не указан", "не указано"} else empty


def render_item_line(table: str, item: dict) -> str:
    """Строка заметки в том же виде, в каком её пишет конвейер (`meetings_process.py`)."""
    text = " ".join(str(item.get("text") or "").split())
    if item.get("status") == "rejected":
        text = f"~~{text}~~"  # отклонённое не удаляем: так отклонение можно отменить
    stamp = str(item.get("evidence_time") or "")
    if table == "tasks":
        mark = "x" if item.get("status") == "done" else " "
        owner = _clean_cell(item.get("owner"), "не указано")
        deadline = _clean_cell(item.get("deadline"), "не указан")
        source = f"; `⏱ {stamp}`" if stamp else ""
        return f"- [{mark}] {text} — **ответственный:** {owner}; **срок:** {deadline}{source}"
    source = f" — `⏱ {stamp}`" if stamp else ""
    return f"- {text}{source}"


def _mermaid_label(value: str) -> str:
    """Так же, как meetings_process.mermaid_text: подпись узла схемы без служебных символов, до 100 знаков."""
    text = re.sub(r"[\[\](){}\"']", "", str(value)).replace("\n", " ").strip()
    return text[:100] or "Не указано"


def _refresh_schemes(text: str, table: str, old: dict, new: dict) -> str:
    """Обновляет подписи в схемах Mermaid заметки (карта встречи, схема решений, план задач) после правки текста или срока.
    Блоки находятся по старой подписи; не найденное остаётся как есть."""
    old_label, new_label = _mermaid_label(old.get("text", "")), _mermaid_label(new.get("text", ""))
    old_due, new_due = (old.get("deadline") or "").strip(), (new.get("deadline") or "").strip()
    if old_label == new_label and (table != "tasks" or old_due == new_due):
        return text

    def fix_block(match: re.Match) -> str:
        out = []
        for line in match.group(0).split("\n"):
            if f"[{old_label}]" in line:
                line = line.replace(f"[{old_label}]", f"[{new_label}]", 1)
            elif table == "tasks" and line.startswith(f"  {old_label} :milestone,"):
                rest = line[len(f"  {old_label} :milestone,"):]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", new_due):
                    rest = re.sub(r"\d{4}-\d{2}-\d{2}", new_due, rest, count=1)
                    line = f"  {new_label} :milestone,{rest}"
                else:
                    continue  # срока больше нет — вехи на плане быть не может
            out.append(line)
        return "\n".join(out)

    return re.sub(r"(?ms)^```mermaid\n.*?^```", fix_block, text)


def update_item_line(note: Path, table: str, old: dict, new: dict) -> str:
    """Переносит правку задачи или решения в строку заметки.

    Возвращает "updated", "unchanged" или "missing" (строки нет: заметку правили вручную).
    Остальной текст заметки не затрагивается; запись атомарная."""
    heading = ITEM_SECTIONS[table]
    text = Path(note).read_text(encoding="utf-8")
    section = re.search(rf"(?ms)^##[ \t]+{re.escape(heading)}[ \t]*\n(.*?)(?=^##[ \t]+|\Z)", text)
    if not section:
        return "missing"
    target = _plain(old.get("text", ""))
    lines = section.group(1).split("\n")
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("- "):
            continue
        if _plain(storage.parse_fact(stripped[2:])["text"]) != target:
            continue
        indent = line[: len(line) - len(stripped)]
        rendered = indent + render_item_line(table, new)
        if rendered == line:
            return "unchanged"
        lines[index] = rendered
        updated = _refresh_schemes(text[: section.start(1)] + "\n".join(lines) + text[section.end(1):], table, old, new)
        temp = Path(note).with_name(f".{Path(note).name}.tmp")
        temp.write_text(updated, encoding="utf-8")
        os.replace(temp, note)
        return "updated"
    return "missing"


def meeting_audio(meeting: Meeting) -> Path | None:
    if not meeting.source_audio:
        return None
    for root in (AUDIO_ROOT, ARCHIVE_ROOT):
        if not root.exists():
            continue
        direct = root / meeting.source_audio
        if direct.is_file():
            return direct
        found = next((p for p in root.rglob(meeting.source_audio) if p.is_file()), None)
        if found:
            return found
    return None


def obsidian_url(path: str | Path) -> str:
    """Ссылка по абсолютному пути файла: Obsidian сам находит хранилище, в котором он лежит.
    Имя папки хранилища не годится: Obsidian ищет зарегистрированное имя, и оно может не совпасть."""
    path = Path(str(path)).expanduser()
    if not path.is_absolute():
        path = (OBSIDIAN_VAULT_ROOT / path).resolve()
    query = urllib.parse.urlencode({"path": path.as_posix()}, quote_via=urllib.parse.quote)
    return f"obsidian://open?{query}"


def note_relative_path(path: str | Path) -> str:
    path = Path(str(path))
    try:
        return path.relative_to(OBSIDIAN_VAULT_ROOT).as_posix()
    except ValueError:
        return path.name


def transcript_file(meeting: Meeting) -> Path | None:
    match = re.search(r"\[\[Транскрипты/([^\]|]+)", meeting.content)
    if match:
        candidate = meeting.path.parent / "Транскрипты" / match.group(1)
        if candidate.exists():
            return candidate
        with_ext = candidate.with_name(candidate.name + ".md")
        if with_ext.exists():
            return with_ext
    return None


def meeting_transcript(meeting: Meeting) -> str:
    path = transcript_file(meeting)
    if path:
        _, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        return body.partition("## Текст с таймкодами")[2].strip()
    legacy = re.split(r"(?m)^## Полная стенограмма\s*$", meeting.content, maxsplit=1)
    return legacy[1].strip() if len(legacy) == 2 else ""


def transcript_segments(meeting: Meeting) -> list[dict]:
    """Реплики стенограммы; `i` — порядковый номер строки с таймкодом (по нему правятся метки говорящих)."""
    body = meeting_transcript(meeting)
    segments = diarize.parse(body)
    # текст без таймкодов (старые заметки) показываем одной репликой
    if not segments:
        text = " ".join(line.strip() for line in body.splitlines() if line.strip())
        if text:
            segments.append({"i": 0, "t": "", "sec": 0, "speaker": "", "text": text})
    return [s for s in segments if s["text"]]


def _transcript_target(meeting: Meeting) -> tuple[Path, str]:
    """Файл и заголовок, после которого идёт текст с таймкодами."""
    path = transcript_file(meeting)
    if path:
        return path, "## Текст с таймкодами"
    if re.search(r"(?m)^## Полная стенограмма\s*$", meeting.content):
        return meeting.path, "## Полная стенограмма"
    raise ValueError("Стенограмма не найдена")


def rewrite_speakers(meeting: Meeting, build_labels) -> list[str]:
    """Перезаписывает метки говорящих. `build_labels(segments)` возвращает метку для каждой реплики."""
    path, heading = _transcript_target(meeting)
    text = path.read_text(encoding="utf-8")
    head, sep, body = text.partition(heading)
    if not sep:
        raise ValueError("Стенограмма не найдена")
    labels = build_labels(diarize.parse(body))
    new_text = head + sep + diarize.relabel(body, labels)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(new_text, encoding="utf-8")
    temp.replace(path)
    return labels


def change_speakers(meeting: Meeting, rename: dict[str, str], segments: dict[int, str]) -> list[str]:
    """Переименование говорящего во всей записи и/или смена говорящего у отдельных реплик."""
    def build(parsed: list[dict]) -> list[str]:
        labels = [rename.get(seg["speaker"], seg["speaker"]) if seg["speaker"] else "" for seg in parsed]
        for index, label in segments.items():
            if 0 <= index < len(labels):
                labels[index] = label
        return labels
    return rewrite_speakers(meeting, build)


@lru_cache(maxsize=256)
def _cached_text(path: str, mtime_ns: int) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def transcript_text_cached(meeting: Meeting) -> str:
    path = transcript_file(meeting)
    if not path:
        return meeting_transcript(meeting)
    try:
        return _cached_text(str(path), path.stat().st_mtime_ns)
    except OSError:
        return ""


def note_sections(meeting: Meeting) -> dict[str, str]:
    return parse_note_sections(meeting.content)


def summary_bullets(meeting: Meeting, limit: int = 8) -> list[str]:
    sections = note_sections(meeting)
    body = next((sections[name] for name in SUMMARY_SECTIONS if name in sections), "")
    items = storage.bullet_lines(body)
    if not items and body.strip() and body.strip() not in storage.PLACEHOLDERS:
        items = [line.strip() for line in body.splitlines() if line.strip() and not line.startswith(("```", ">"))]
    return items[:limit]


def fact_items(meeting: Meeting, *names: str) -> list[dict]:
    sections = note_sections(meeting)
    body = next((sections[name] for name in names if name in sections), "")
    items = []
    for line in storage.bullet_lines(body):
        fact = storage.parse_fact(line)
        if fact["text"]:
            items.append({**fact, "sec": to_seconds(fact["evidence_time"])})
    return items


def sections(meeting: Meeting) -> list[dict]:
    """Фрагменты заметки для индекса."""
    chunks = []
    for part in re.split(r"(?m)^##\s+", meeting.content)[1:]:
        heading, _, body = part.partition("\n")
        body = re.sub(r"(?m)^<!--\s*md-view .*?-->[ \t]*\n?", "", body).strip()  # служебная строка взгляда
        if not body or heading.strip() == "Карта встречи":
            continue
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and not p.startswith("```")]
        if heading.strip() == "Закладки":   # у каждой закладки свой таймкод: ищется и открывается по отдельности
            for line in storage.bullet_lines(body):
                chunks.append(make_chunk(meeting, "Закладки", "Закладка: " + line))
            continue
        if heading.strip() == "Вложения":   # у каждого вложения свой фрагмент с названием: «найдено на фото доски»
            from . import attachments
            for text in attachments.chunk_texts(body):
                chunks.append(make_chunk(meeting, "Вложения", text))
            continue
        buffer = ""
        for paragraph in paragraphs:
            candidate = f"{buffer}\n\n{paragraph}".strip()
            if len(candidate) > 1100 and buffer:
                chunks.append(make_chunk(meeting, heading.strip(), buffer))
                buffer = paragraph
            else:
                buffer = candidate
        if buffer:
            chunks.append(make_chunk(meeting, heading.strip(), buffer))
    transcript = meeting_transcript(meeting)
    if transcript and "## Полная стенограмма" not in meeting.content:
        # Фрагменты стенограммы режем по строкам с таймкодами, чтобы источник вёл на точное место.
        buffer, first_stamp = "", ""
        for line in transcript.splitlines():
            line = line.strip()
            if not line:
                continue
            stamp = diarize.TS_LINE.match(line)
            if stamp and not first_stamp:
                first_stamp = stamp.group(1)
            if len(buffer) + len(line) > 1000 and buffer:
                chunks.append(make_chunk(meeting, "Полная стенограмма", buffer, first_stamp))
                buffer, first_stamp = "", stamp.group(1) if stamp else ""
            buffer = (buffer + "\n" + line).strip()
        if buffer:
            chunks.append(make_chunk(meeting, "Полная стенограмма", buffer, first_stamp))
    return chunks


def make_chunk(meeting: Meeting, section: str, text: str, timestamp: str = "") -> dict:
    if not timestamp:
        match = re.search(r"\[(\d{2}:\d{2}:\d{2})\]|⏱\s*(\d{2}:\d{2}:\d{2})", text)
        timestamp = next((v for v in match.groups() if v), "") if match else ""
    return {
        "text": text, "title": meeting.title, "date": meeting.date, "recorded_at": meeting.recorded_at,
        "time": meeting.time, "duration": meeting.duration, "project": meeting.project,
        "project_id": meeting.project_id, "source_type": meeting.source_type, "record_type": meeting.record_type,
        "participants": meeting.participants, "section": section, "timestamp": timestamp,
        "path": str(meeting.path), "meeting_id": meeting.id,
    }


def corpus_signature(meetings: list[Meeting]) -> str:
    values = []
    for meeting in sorted(meetings, key=lambda m: str(m.path)):
        try:
            stat = meeting.path.stat()
            values.append(f"{meeting.path}:{stat.st_mtime_ns}:{stat.st_size}")
            transcript = transcript_file(meeting)
            if transcript:
                tstat = transcript.stat()
                values.append(f"{transcript}:{tstat.st_mtime_ns}:{tstat.st_size}")
        except OSError:
            continue
    values.append(f"index-version:{INDEX_VERSION}")
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


# --------------------------------------------------------------------------- эмбеддинги

class HashingEncoder:
    """Детерминированный кодировщик для тестов и разработки без модели (MEMORY_DESK_FAKE_EMBED=1)."""

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        import numpy as np
        out = np.zeros((len(texts), EMBEDDING_SIZE), dtype="float32")
        for row, text in enumerate(texts):
            for word in re.findall(r"\w+", text.casefold()):
                stem = word[:5]
                out[row, int(hashlib.md5(stem.encode()).hexdigest(), 16) % EMBEDDING_SIZE] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


def make_encoder():
    if os.environ.get("MEMORY_DESK_FAKE_EMBED") == "1":
        return HashingEncoder()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL, cache_folder=str(config.MODEL_CACHE))


class SearchIndex:
    """Qdrant + embedding-модель. Загружается в фоне, чтобы интерфейс открывался мгновенно."""

    def __init__(self) -> None:
        self.state = "starting"   # starting | loading_model | indexing | ready | error
        self.mode = "persistent"  # persistent | memory_fallback
        self.error = ""
        self.encoder = None
        self.client = None
        self.lock = threading.RLock()
        # состояние ручной переиндексации (кнопка «Переиндексировать»)
        self.reindex: dict = {"state": "idle", "done": 0, "total": 0, "records": 0, "message": "", "startedAt": 0.0, "finishedAt": 0.0}
        self._pending: list[Meeting] | None = None
        self._thread: threading.Thread | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def start(self, meetings: list[Meeting]) -> None:
        self._pending = meetings
        self._thread = threading.Thread(target=self._boot, name="search-index", daemon=True)
        self._thread.start()

    def _open_client(self):
        from qdrant_client import QdrantClient
        qdrant_dir = Path(os.environ.get("MEETING_QDRANT_DIR") or config.DATA_DIR / "qdrant")
        try:
            return QdrantClient(path=str(qdrant_dir))
        except RuntimeError as exc:
            if "already accessed by another instance" not in str(exc):
                raise
            # Каталог занят другим процессом (например, старым экземпляром) — работаем с индексом в памяти.
            self.mode = "memory_fallback"
            return QdrantClient(location=":memory:")

    def _boot(self) -> None:
        try:
            self.state = "loading_model"
            self.encoder = make_encoder()
            with self.lock:
                self.client = self._open_client()
            self.state = "indexing"
            self.sync(self._pending or [], force=False)
            self.state = "ready"
        except Exception as exc:  # noqa: BLE001 — любая ошибка индекса не должна ронять кабинет
            self.state = "error"
            self.error = f"{type(exc).__name__}: {exc}"

    def _signature_path(self) -> Path:
        return config.DATA_DIR / "signature.txt"

    def sync(self, meetings: list[Meeting], force: bool = False, progress=None) -> bool:
        """Перестраивает индекс, если заметки изменились (или всегда при force). Возвращает True, если перестроил.
        progress(done, total) вызывается по мере расчёта векторов."""
        from qdrant_client import models
        with self.lock:
            if self.client is None or self.encoder is None:
                self._pending = meetings
                return False
            signature = corpus_signature(meetings)
            path = self._signature_path()
            current = path.read_text().strip() if path.exists() else ""
            if not force and current == signature and self.client.collection_exists(COLLECTION):
                return False
            if self.client.collection_exists(COLLECTION):
                self.client.delete_collection(COLLECTION)
            self.client.create_collection(
                collection_name=COLLECTION,
                vectors_config=models.VectorParams(size=EMBEDDING_SIZE, distance=models.Distance.COSINE),
            )
            payloads = [chunk for meeting in meetings for chunk in sections(meeting)]
            if progress:
                progress(0, len(payloads))
            if payloads:
                texts = [f"passage: {p['title']}\n{p['section']}\n{p['text']}" for p in payloads]
                vectors = []
                for start in range(0, len(texts), 32):
                    vectors += self.encoder.encode(texts[start:start + 32], normalize_embeddings=True, show_progress_bar=False).tolist()
                    if progress:
                        progress(min(start + 32, len(texts)), len(texts))
                points = [models.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{p['path']}:{p['section']}:{i}:{p['text'][:80]}")),
                    vector=vector, payload=p,
                ) for i, (p, vector) in enumerate(zip(payloads, vectors))]
                self.client.upsert(COLLECTION, points=points, wait=True)
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(signature)
            return True

    def collection_ready(self) -> bool:
        with self.lock:
            try:
                return bool(self.client) and self.client.collection_exists(COLLECTION)
            except Exception:  # noqa: BLE001
                return False

    def search(self, query: str, paths: set[str] | None = None, limit: int = 4) -> list[dict]:
        from qdrant_client import models
        if not self.ready:
            raise IndexNotReady(self.state)
        if paths is not None and not paths:
            return []
        with self.lock:
            vector = self.encoder.encode([f"query: {query}"], normalize_embeddings=True)[0].tolist()
            query_filter = None
            if paths is not None:
                query_filter = models.Filter(must=[
                    models.FieldCondition(key="path", match=models.MatchAny(any=sorted(paths)))])
            result = self.client.query_points(
                collection_name=COLLECTION, query=vector, query_filter=query_filter, limit=limit, with_payload=True)
        found = [{**point.payload, "score": float(point.score)} for point in result.points]
        return rerank(query, found)[:limit]


class IndexNotReady(RuntimeError):
    pass


def rerank(query: str, found: list[dict]) -> list[dict]:
    query_cf = query.casefold()
    boosts = {
        "решени": (DECISION_SECTION, "Схема решений"),
        "задач": ("Задачи", "Следующие шаги"),
        "следующ": ("Следующие шаги", "Задачи"),
        "риск": ("Риски",),
        "итог": ("Краткое резюме", "Кратко", DECISION_SECTION),
        "вопрос": ("Открытые вопросы",),
    }
    for item in found:
        item["rank_score"] = item.get("score", 0.0)
        for needle, preferred in boosts.items():
            if needle in query_cf and item.get("section") in preferred:
                item["rank_score"] += 0.22
    return sorted(found, key=lambda item: item["rank_score"], reverse=True)


# --------------------------------------------------------------------------- ответы

def source_label(source: dict) -> str:
    try:
        parsed = datetime.strptime(str(source.get("date", ""))[:10], "%Y-%m-%d")
        date_label = f"{parsed.day} {RU_MONTHS[parsed.month]} {parsed.year}"
    except ValueError:
        date_label = str(source.get("date") or "дата не указана")
    time_label = f" в {source['time']}" if source.get("time") else ""
    return f"встреча {date_label}{time_label} — {source['title']}"


def overview_chunks(meetings: list[Meeting], paths: list[str]) -> list[dict]:
    wanted = set(paths)
    result = []
    for meeting in meetings:
        if str(meeting.path) not in wanted:
            continue
        by_section = {chunk["section"]: chunk for chunk in sections(meeting)}
        for name in OVERVIEW_SECTIONS:
            if name in by_section:
                result.append(by_section[name])
    return result[:8]


def retrieve(index: SearchIndex, meetings: list[Meeting], question: str, previous_paths: list[str] | None = None,
             allowed_paths: set[str] | None = None) -> tuple[list[dict], str]:
    """Сначала выбирает встречу, затем возвращает обзор или точные фрагменты."""
    question_cf = question.casefold()
    previous_paths = [p for p in (previous_paths or []) if allowed_paths is None or p in allowed_paths]
    follow_up = bool(re.search(r"\b(этой|эту|эта|этот|этом|ней|там|данной)\b", question_cf))
    overview = bool(re.search(r"(что было|о ч[её]м|расскажи|содержание|итог.*встреч|про встреч)", question_cf))
    if previous_paths and (follow_up or overview):
        return overview_chunks(meetings, previous_paths[:1]), "Продолжение по выбранной встрече"
    if allowed_paths is not None and len(allowed_paths) == 1 and overview:
        return overview_chunks(meetings, list(allowed_paths)), "Обзор встречи"

    candidates = index.search(question, paths=allowed_paths, limit=12)
    candidates = [c for c in candidates if c.get("rank_score", c["score"]) >= MIN_ANSWER_SCORE]
    if not candidates:
        return [], "Ничего не найдено"
    scores: dict[str, float] = {}
    for rank, item in enumerate(candidates):
        path = str(item["path"])
        scores[path] = scores.get(path, 0.0) + item.get("rank_score", item["score"]) / (1 + rank * 0.12)
    selected = max(scores, key=scores.get)
    if overview:
        return overview_chunks(meetings, [selected]), "Обзор найденной встречи"
    focused = [c for c in candidates if str(c["path"]) == selected]
    other = [c for c in candidates if str(c["path"]) != selected]
    return (focused[:3] + other[:1])[:4], "Точный поиск по фрагментам"


NO_EVIDENCE = ("В записях этой области нет фрагментов, которые подтверждают ответ. "
               "Попробуйте уточнить вопрос или расширить область поиска.")


def clean_answer(text: str) -> str:
    text = re.sub(r"\s*\[встреча [^\]]+\]", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def answer_question(question: str, sources: list[dict], mode: str = "") -> str:
    if not sources:
        return NO_EVIDENCE
    if re.search(r"(на какой встреч|где обсуждал|в какой встреч|найти встреч|встречу.*(где|котор))", question.casefold()):
        labels = list(dict.fromkeys(source_label(s) for s in sources))
        return "Это обсуждалось здесь: " + "; ".join(labels) + "."
    if "обзор" in mode.casefold() or "продолжение" in mode.casefold():
        by_section = {s.get("section"): s for s in sources}
        summary_source = by_section.get("Кратко") or by_section.get("Краткое резюме") or sources[0]
        summary = re.sub(r"\s+", " ", summary_source["text"]).strip()
        if len(summary) > 650:
            summary = summary[:647].rsplit(" ", 1)[0] + "…"
        blocks = [f"**Кратко:** {summary}"]
        for name in (DECISION_SECTION, "Задачи", "Следующие шаги", "Риски"):
            source = by_section.get(name)
            if not source:
                continue
            lines = [storage.parse_fact(l.strip()[2:])["text"] for l in source["text"].splitlines()
                     if l.strip().startswith("- ")][:3]
            lines = [l for l in lines if l and l not in storage.PLACEHOLDERS]
            if lines:
                blocks.append(f"**{name}:**\n" + "\n".join(f"- {l}" for l in lines))
        return clean_answer("\n\n".join(blocks[:4]))
    context = []
    for source in sources:
        context.append(f"[{source_label(source)}] | {source['section']} | "
                       f"таймкод {source.get('timestamp') or 'не указан'}\n{source['text'][:1200]}")
    prompt = f"""Ты — локальный помощник по архиву встреч.
Отвечай только на основании источников ниже. Не выдавай обсуждение или предложение за принятое решение.
Если подтверждения недостаточно, прямо скажи об этом. Не используй ссылки вида [1], [2] и не вставляй метки встреч.
Не показывай ход рассуждений, анализ источников и служебные комментарии.
Дай только итог: максимум 3 коротких предложения или 3 пункта. Не добавляй раздел «Источники» — интерфейс покажет их отдельно.
Отвечай по-русски, конкретно и без домыслов.

Вопрос: {question}
Режим поиска: {mode}

Источники:
{chr(10).join(context)}

ОТВЕТ:
"""
    payload = {
        "model": config.OLLAMA_MODEL, "stream": False,
        # raw обходит chat-template Qwen, который на некоторых версиях Ollama игнорирует think=false.
        "raw": True, "prompt": prompt,
        "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 160},
    }
    request = urllib.request.Request(
        f"{config.OLLAMA_URL}/api/generate", data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = json.load(response).get("response", "")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        refused = isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower()
        why = "она не запущена" if refused else str(reason)
        return f"Локальная модель Ollama сейчас недоступна ({why}). Источники ниже найдены в архиве."
    if "</think>" in raw:
        raw = raw.rsplit("</think>", 1)[-1]
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    return clean_answer(raw) or "Ответ не получен."
