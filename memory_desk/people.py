"""Карточки людей в Obsidian: `Люди/<Имя>.md`.

Файл — источник правды для должности, организации, псевдонимов и заметки о человеке (база только копия),
поэтому эти данные переживают потерю или откат SQLite. Записи и карточка находят друг друга по имени:
идентификатор человека считается от имени (`storage.person_id_for`).
Всё, что в файле написано сверх известных полей и раздела «Заметки», при правках сохраняется.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import knowledge, storage

KNOWN = ("type", "person_id", "name", "role", "organization", "aliases")
MAX_NAME = 120
MAX_FIELD = 200
MAX_NOTES = 5000
MAX_ALIASES = 10


class PersonError(ValueError):
    """Ошибка ввода; текст понятен пользователю."""


def people_dir() -> Path:
    return knowledge.WORKSPACE_DIR / "Люди"


def clean_line(value: object, limit: int = MAX_FIELD) -> str:
    text = " ".join(str(value or "").split()).replace('"', "'")
    return text[:limit]


def clean_aliases(values: object) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,;\n]", values)
    out: list[str] = []
    for value in values or []:
        alias = clean_line(value, 80)
        if alias and alias.casefold() not in {a.casefold() for a in out}:
            out.append(alias)
    return out[:MAX_ALIASES]


def clean_notes(value: object) -> str:
    lines = [line.rstrip() for line in str(value or "").replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines).strip()
    # заголовок второго уровня внутри заметки разорвал бы файл на разделы
    text = re.sub(r"(?m)^##\s+", "### ", text)
    return text[:MAX_NOTES]


def validate_name(name: str) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise PersonError("Имя не может быть пустым")
    if len(name) > MAX_NAME:
        raise PersonError(f"Имя слишком длинное: не больше {MAX_NAME} знаков")
    if re.search(r"[\\/:*?\"<>|\x00-\x1f]", name):
        raise PersonError("В имени есть недопустимые символы: \\ / : * ? \" < > |")
    if not re.search(r"\w", name):
        raise PersonError("В имени должна быть хотя бы одна буква")
    return name


def card_path(name: str) -> Path:
    return people_dir() / f"{name}.md"


def load_cards() -> list[dict]:
    cards: list[dict] = []
    directory = people_dir()
    if not directory.exists():
        return cards
    for path in sorted(directory.glob("*.md")):
        try:
            meta, body = knowledge.parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if str(meta.get("type", "")) != "person":
            continue
        name = " ".join(str(meta.get("name") or path.stem).split())
        if not name:
            continue
        aliases = meta.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        cards.append({"name": name, "role": clean_line(meta.get("role")), "organization": clean_line(meta.get("organization")),
                      "aliases": clean_aliases(aliases), "notes": knowledge.parse_note_sections(body).get("Заметки", "")[:MAX_NOTES],
                      "path": str(path)})
    return cards


def _frontmatter(person_id: str, fields: dict, extra: list[str]) -> str:
    lines = ["---", "type: person", f'person_id: "{person_id}"', f'name: "{fields["name"]}"']
    if fields.get("role"):
        lines.append(f'role: "{fields["role"]}"')
    if fields.get("organization"):
        lines.append(f'organization: "{fields["organization"]}"')
    if fields.get("aliases"):
        lines.append("aliases:")
        lines.extend(f'  - "{a}"' for a in fields["aliases"])
    lines.extend(extra)
    lines.append("---")
    return "\n".join(lines)


def _split_existing(text: str) -> tuple[list[str], str]:
    """(неизвестные строки шапки, тело) существующего файла; для чужой шапки неизвестные строки остаются как есть."""
    if not text.startswith("---\n"):
        return [], text
    end = text.find("\n---\n", 4)
    if end == -1:
        return [], text
    extra: list[str] = []
    skip_list = False
    for line in text[4:end].splitlines():
        if skip_list and line.startswith("  - "):
            continue
        skip_list = False
        match = re.match(r"^([\w-]+):", line)
        if match and match.group(1) in KNOWN:
            skip_list = not line.split(":", 1)[1].strip()
            continue
        if line.strip():
            extra.append(line)
    return extra, text[end + 5:]


def _replace_notes(body: str, notes: str, name: str) -> str:
    block = "## Заметки\n\n" + (notes + "\n" if notes else "")
    if re.search(r"(?m)^##\s+Заметки\s*$", body):
        return re.sub(r"(?ms)^##\s+Заметки\s*$.*?(?=^##\s|\Z)", lambda _m: block + "\n", body, count=1).rstrip() + "\n"
    if not body.strip():
        return f"# {name}\n\n{block}"
    return body.rstrip() + "\n\n" + block


def write_card(name: str, fields: dict) -> Path:
    """Создаёт или обновляет `Люди/<Имя>.md`. `fields`: role, organization, aliases, notes (ключи, которых нет, не меняются)."""
    name = validate_name(name)
    path = card_path(name)
    current = {"role": "", "organization": "", "aliases": [], "notes": ""}
    extra: list[str] = []
    body = ""
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, _ = knowledge.parse_frontmatter(text)
        if str(meta.get("type", "")) != "person":
            raise PersonError("Файл с таким именем уже есть в папке «Люди», но это не карточка человека")
        current.update(role=clean_line(meta.get("role")), organization=clean_line(meta.get("organization")),
                       aliases=clean_aliases(meta.get("aliases") or []),
                       notes=knowledge.parse_note_sections(knowledge.parse_frontmatter(text)[1]).get("Заметки", ""))
        extra, body = _split_existing(text)
    merged = {
        "name": name,
        "role": clean_line(fields["role"]) if "role" in fields else current["role"],
        "organization": clean_line(fields["organization"]) if "organization" in fields else current["organization"],
        "aliases": clean_aliases(fields["aliases"]) if "aliases" in fields else current["aliases"],
    }
    notes = clean_notes(fields["notes"]) if "notes" in fields else current["notes"]
    content = _frontmatter(storage.person_id_for(name), merged, extra) + "\n\n" + _replace_notes(body.lstrip("\n"), notes, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".card-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def create(name: str, fields: dict) -> dict:
    """Новый человек: строка в базе и карточка в Obsidian. Если человек уже есть (из записей), карточка дополняет его."""
    name = validate_name(name)
    found = storage.one("SELECT id,name FROM people WHERE id=?", (storage.person_id_for(name),))
    path = write_card(found["name"] if found else name, fields)
    card = next(c for c in load_cards() if c["path"] == str(path))
    storage.sync_person_cards([card])
    return {"id": storage.person_id_for(name), "created": found is None, "path": str(path)}


def update(person_id: str, fields: dict) -> bool:
    """Правка полей существующего человека; файл создаётся, если его ещё не было."""
    row = storage.one("SELECT name FROM people WHERE id=?", (person_id,))
    if not row:
        return False
    path = write_card(row["name"], fields)
    card = next(c for c in load_cards() if c["path"] == str(path))
    storage.sync_person_cards([card])
    return True
