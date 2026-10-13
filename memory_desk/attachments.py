"""Вложения к записи: текстовая заметка и фото доски, слайда, листа. Текст с фото распознаётся локально (ocr.py).

Файлы лежат в хранилище Obsidian рядом с заметкой: `<папка заметки>/Вложения/<имя заметки>/<id>-<имя файла>`, поэтому
`![[…]]` в самой заметке показывает снимок в Obsidian. Сведения о вложении — в разделе `## Вложения` заметки:

    ### Доска после планёрки.jpg
    ![[Claude/Встречи/…/Вложения/…/ab12cd34-доска.jpg|480]]
    > фото · Vision · черновик
    распознанный текст

Заметка — единственный источник правды, как и для закладок: потеря базы или «Переиндексировать» вложения не
затрагивают, а пересборка по шаблону переносит раздел как есть. Распознанный текст — черновик, пока человек не
поправил его («проверено»). Строки раздела, которые не похожи на вложение (дописаны руками), при правках сохраняются.
Сами файлы никуда не отправляются.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import knowledge, ocr

TITLE = "Вложения"
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff"}
CONVERT_TYPES = {".heic", ".heif"}          # снимок iPhone: переводится в JPEG (macOS, `sips`)
TEXT_TYPES = {".txt", ".md", ".markdown"}
MAX_BYTES = 25 * 1024 * 1024
MAX_PER_RECORD = 30
MAX_TEXT = 30000
MAX_NAME = 80
_ID = re.compile(r"^[0-9a-f]{8}$")
_SECTION = re.compile(r"(?ms)^##[ \t]+" + TITLE + r"[ \t]*\n.*?(?=^##[ \t]+|\Z)")
_EMBED = re.compile(r"^!?\[\[(?P<path>[^\]|]+)(?:\|[^\]]*)?\]\]\s*$")
_KIND = {"фото": "image", "текст": "text"}
_KIND_WORD = {v: k for k, v in _KIND.items()}
_lock = threading.Lock()


class AttachmentError(RuntimeError):
    """Вложение не добавлено; текст понятен пользователю. `status` — код ответа API."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Entry:
    id: str
    name: str
    kind: str = "image"            # image | text
    path: str = ""                 # путь файла от корня хранилища Obsidian
    engine: str = ""
    draft: bool = False
    text: str = ""
    raw: str | None = None         # раздел, который не похож на вложение: переносится как есть
    order: int = field(default=0, repr=False)


# --------------------------------------------------------------------------- разбор и запись раздела

_HEADING = re.compile(r"^ {0,3}#")
_ESCAPED = re.compile(r"^\\ {0,3}#")


def _escape(text: str) -> str:
    """Строка текста, похожая на заголовок Markdown, получает «\\»: иначе она стала бы разделом заметки."""
    return "\n".join(("\\" + line) if _HEADING.match(line) else line for line in text.splitlines())


def _unescape(text: str) -> str:
    return "\n".join(line[1:] if _ESCAPED.match(line) else line for line in text.splitlines())


def clean_name(value: str) -> str:
    name = re.sub(r"[\x00-\x1f\[\]#|^:*?\"<>\\/]", " ", str(value or ""))
    return " ".join(name.split())[:MAX_NAME].strip(" .") or "вложение"


def _parse_meta(line: str) -> tuple[str, str, bool] | None:
    if not line.startswith(">"):
        return None
    parts = [p.strip() for p in line.lstrip("> ").split("·")]
    if not parts or parts[0] not in _KIND:
        return None
    return _KIND[parts[0]], (parts[1] if len(parts) > 2 else ""), "черновик" in parts[-1].casefold()


def _entry_id(path: str) -> str:
    match = re.match(r"([0-9a-f]{8})-", Path(path).name)
    return match.group(1) if match else hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]


def parse_body(body: str) -> list[Entry]:
    entries: list[Entry] = []
    for index, part in enumerate(re.split(r"(?m)^###[ \t]+", body)):
        if index == 0:
            if part.strip():
                entries.append(Entry("", "", raw=part.strip(), order=0))
            continue
        heading, _, rest = part.partition("\n")
        lines = rest.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        match = _EMBED.match(lines[0].strip()) if lines else None
        if not match:
            entries.append(Entry("", "", raw="### " + part.rstrip(), order=index))
            continue
        path = match.group("path").strip()
        meta = _parse_meta(lines[1].strip()) if len(lines) > 1 else None
        text_lines = lines[2:] if meta else lines[1:]
        kind = meta[0] if meta else ("text" if Path(path).suffix.lower() in TEXT_TYPES | {".txt"} else "image")
        entries.append(Entry(_entry_id(path), heading.strip() or Path(path).name, kind, path,
                             meta[1] if meta else "", meta[2] if meta else False,
                             _unescape("\n".join(text_lines)).strip(), order=index))
    return entries


def parse_content(content: str) -> list[Entry]:
    match = _SECTION.search(content)
    return parse_body(match.group(0).partition("\n")[2]) if match else []


def real(entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if e.raw is None]


def render(entries: list[Entry]) -> str:
    blocks = []
    for e in entries:
        if e.raw is not None:
            blocks.append(e.raw)
            continue
        embed = f"![[{e.path}|480]]" if e.kind == "image" else f"[[{e.path}]]"
        meta = " · ".join([_KIND_WORD[e.kind], e.engine or ("файл" if e.kind == "text" else "вручную"),
                           "черновик" if e.draft else "проверено"])
        parts = [f"### {e.name}", embed, f"> {meta}"]
        if e.text:
            parts.append(_escape(e.text))
        blocks.append("\n".join(parts))
    return "## " + TITLE + "\n\n" + "\n\n".join(blocks).rstrip() + "\n"


def section_text(content: str) -> str:
    """Раздел «Вложения» как есть — чтобы пересборка заметки по шаблону его не теряла."""
    match = _SECTION.search(content)
    return match.group(0).rstrip() + "\n" if match else ""


def _place(text: str, section: str) -> str:
    """Раздел встаёт перед первым «Взглядом», а без них — перед «Стенограммой»."""
    existing = _SECTION.search(text)
    if existing:
        return text[:existing.start()] + section + "\n" + text[existing.end():].lstrip("\n")
    anchor = re.search(r"(?m)^##[ \t]+(?:Взгляд: |(?:Полная )?[Сс]тенограмма[ \t]*$)", text)
    if anchor:
        return text[:anchor.start()] + section + "\n" + text[anchor.start():]
    return text.rstrip() + "\n\n" + section


def _write(meeting: knowledge.Meeting, text: str) -> None:
    temp = meeting.path.with_name(f".{meeting.path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(meeting.path)


# --------------------------------------------------------------------------- файлы

def folder(meeting: knowledge.Meeting) -> Path:
    return meeting.path.parent / TITLE / meeting.path.stem


def _vault_path(file: Path) -> str:
    return knowledge.note_relative_path(file)


def file_of(meeting: knowledge.Meeting, entry: Entry) -> Path | None:
    """Файл вложения на диске; только если он лежит в папке вложений этой записи (путь из заметки не доверяется)."""
    if not entry.path:
        return None
    candidate = (knowledge.OBSIDIAN_VAULT_ROOT / entry.path).resolve()
    try:
        candidate.relative_to(folder(meeting).resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def media_type(entry: Entry, file: Path) -> str:
    return IMAGE_TYPES.get(file.suffix.lower(), "text/plain; charset=utf-8" if entry.kind == "text" else "application/octet-stream")


def _convert_heic(data: bytes, directory: Path) -> bytes:
    if sys.platform != "darwin" or not shutil.which("sips"):
        raise AttachmentError("Снимок в формате HEIC можно добавить на macOS; на другой системе сохраните его как JPEG")
    source, target = directory / "in.heic", directory / "out.jpg"
    source.write_bytes(data)
    done = subprocess.run(["sips", "-s", "format", "jpeg", str(source), "--out", str(target)], capture_output=True, timeout=120)
    if done.returncode != 0 or not target.is_file():
        raise AttachmentError("Не удалось перевести снимок HEIC в JPEG")
    return target.read_bytes()


def _check_image(data: bytes) -> None:
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:
        raise AttachmentError("Файл не похож на изображение или повреждён") from exc


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AttachmentError("Не удалось прочитать текст: нужна кодировка UTF-8 или Windows-1251")


def _tidy(text: str) -> str:
    return "\n".join(line.rstrip() for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()[:MAX_TEXT]


def _short_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:8]


# --------------------------------------------------------------------------- действия

def _mutate(core, meeting_id: str, change):
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise AttachmentError("Запись не найдена", 404)
    with _lock:
        text = meeting.path.read_text(encoding="utf-8")
        entries = parse_content(text)
        result = change(entries, meeting)
        if entries:
            new_text = _place(text, render(entries))
        else:   # вложений не осталось: раздела в заметке нет, как будто его и не добавляли
            existing = _SECTION.search(text)
            new_text = text[:existing.start()] + text[existing.end():].lstrip("\n") if existing else text
        if new_text != text:
            _write(meeting, new_text)
    if new_text != text:
        core.reload(force=True)
    return result


def _dto(meeting: knowledge.Meeting, entry: Entry) -> dict:
    file = file_of(meeting, entry)
    return {"id": entry.id, "name": entry.name, "kind": entry.kind, "text": entry.text, "engine": entry.engine,
            "draft": entry.draft, "path": entry.path, "exists": bool(file), "size": file.stat().st_size if file else 0,
            "url": f"/api/meetings/{meeting.id}/attachments/{entry.id}/file" if file else None}


def list_for(meeting: knowledge.Meeting) -> list[dict]:
    return [_dto(meeting, e) for e in real(parse_content(meeting.content))]


def add_file(core, meeting_id: str, filename: str, data: bytes) -> dict:
    """Добавляет снимок или текстовый файл. Тот же файл второй раз не дублируется (`already`)."""
    suffix = Path(filename or "").suffix.lower()
    if not data:
        raise AttachmentError("Файл пустой")
    if len(data) > MAX_BYTES:
        raise AttachmentError(f"Файл больше {MAX_BYTES // 1024 // 1024} МБ")
    if suffix in IMAGE_TYPES or suffix in CONVERT_TYPES:
        kind = "image"
    elif suffix in TEXT_TYPES:
        kind = "text"
    else:
        raise AttachmentError("Можно добавить фото (PNG, JPEG, HEIC, WebP, TIFF) или текст (.txt, .md)")
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise AttachmentError("Запись не найдена", 404)
    stem = clean_name(Path(filename).stem)
    directory = folder(meeting)
    if kind == "image":
        if suffix in CONVERT_TYPES:
            import tempfile
            with tempfile.TemporaryDirectory(prefix="md-heic-") as tmp:
                data, suffix = _convert_heic(data, Path(tmp)), ".jpg"
        _check_image(data)
        text, engine, draft, note = "", "", True, ""
    else:
        text, engine, draft, note = _tidy(_decode_text(data)), "файл", False, ""
        if not text:
            raise AttachmentError("В файле нет текста")
        data, suffix = text.encode("utf-8"), ".txt"
    ident = _short_hash(data)
    existing = next((e for e in real(parse_content(meeting.content)) if e.id == ident), None)
    if existing:
        return {**_dto(meeting, existing), "already": True}
    if len(real(parse_content(meeting.content))) >= MAX_PER_RECORD:
        raise AttachmentError(f"Вложений у записи не больше {MAX_PER_RECORD}")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{ident}-{stem}{suffix}"
    target.write_bytes(data)
    warning = ""
    if kind == "image":
        try:
            result = ocr.recognize(target)
            text, engine, warning = _tidy(result.text), result.engine, result.note
        except ocr.OcrError as exc:
            warning = str(exc)
    entry = Entry(ident, stem if kind == "text" else (stem + suffix), kind, _vault_path(target), engine, draft, text)

    def change(entries: list[Entry], _meeting) -> Entry:
        entries.append(entry)
        return entry
    try:
        _mutate(core, meeting_id, change)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    meeting = core.by_id.get(meeting_id) or meeting
    out = {**_dto(meeting, entry), "already": False}
    if warning:
        out["warning"] = warning
    if kind == "image" and not text and not warning:
        out["warning"] = "На снимке текст не найден"
    return out


def add_text(core, meeting_id: str, name: str, text: str) -> dict:
    """Текстовая заметка, набранная в кабинете: хранится так же, как загруженный .txt."""
    body = _tidy(text)
    if not body:
        raise AttachmentError("Текст пустой")
    title = clean_name(name) if str(name or "").strip() else "Заметка"
    return add_file(core, meeting_id, title + ".txt", body.encode("utf-8"))


def update(core, meeting_id: str, att_id: str, text: str | None = None, name: str | None = None) -> dict:
    """Правка текста (после неё он «проверен») и/или названия."""
    def change(entries: list[Entry], meeting) -> Entry:
        entry = next((e for e in real(entries) if e.id == att_id), None)
        if not entry:
            raise AttachmentError("Такого вложения нет", 404)
        if text is not None:
            entry.text = _tidy(text)
            entry.draft = False
        if name is not None and str(name).strip():
            entry.name = clean_name(name)
        return entry
    entry = _mutate(core, meeting_id, change)
    return _dto(core.by_id[meeting_id], entry)


def recognize_again(core, meeting_id: str, att_id: str, force: bool = False) -> dict:
    """Повторное распознавание; правленый вручную текст заменяется только с `force`."""
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise AttachmentError("Запись не найдена", 404)
    entry = next((e for e in real(parse_content(meeting.content)) if e.id == att_id), None)
    if not entry:
        raise AttachmentError("Такого вложения нет", 404)
    if entry.kind != "image":
        raise AttachmentError("Распознавать нужно только фото")
    if entry.text and not entry.draft and not force:
        raise AttachmentError("Текст правили вручную; повторное распознавание его заменит. Подтвердите замену", 409)
    file = file_of(meeting, entry)
    if not file:
        raise AttachmentError("Файл снимка не найден", 404)
    try:
        result = ocr.recognize(file)
    except ocr.OcrError as exc:
        raise AttachmentError(str(exc), 422) from exc

    def change(entries: list[Entry], _meeting) -> Entry:
        found = next((e for e in real(entries) if e.id == att_id), None)
        if not found:
            raise AttachmentError("Такого вложения нет", 404)
        found.text, found.engine, found.draft = _tidy(result.text), result.engine, True
        return found
    updated = _mutate(core, meeting_id, change)
    out = _dto(core.by_id[meeting_id], updated)
    if result.note:
        out["warning"] = result.note
    return out


def remove(core, meeting_id: str, att_id: str) -> bool:
    """Убирает вложение из заметки и стирает его файл."""
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise AttachmentError("Запись не найдена", 404)
    removed: list[Path] = []

    def change(entries: list[Entry], current) -> bool:
        found = next((e for e in real(entries) if e.id == att_id), None)
        if not found:
            return False
        file = file_of(current, found)
        if file:
            removed.append(file)
        entries.remove(found)
        return True
    done = _mutate(core, meeting_id, change)
    for file in removed:
        file.unlink(missing_ok=True)
    directory = folder(meeting)
    try:
        directory.rmdir()           # пустую папку убираем; непустую (чужие файлы) не трогаем
        directory.parent.rmdir()
    except OSError:
        pass
    return done


def erase_files(meeting: knowledge.Meeting) -> int:
    """Стирает все файлы вложений записи (для «стереть запись целиком»); возвращает число файлов."""
    directory = folder(meeting)
    if not directory.is_dir():
        return 0
    count = sum(1 for p in directory.rglob("*") if p.is_file())
    shutil.rmtree(directory, ignore_errors=True)
    try:
        directory.parent.rmdir()
    except OSError:
        pass
    return count


def file_for(core, meeting_id: str, att_id: str) -> tuple[Path, str]:
    meeting = core.by_id.get(meeting_id)
    if not meeting:
        raise AttachmentError("Запись не найдена", 404)
    entry = next((e for e in real(parse_content(meeting.content)) if e.id == att_id), None)
    file = file_of(meeting, entry) if entry else None
    if not entry or not file:
        raise AttachmentError("Файл вложения не найден", 404)
    return file, media_type(entry, file)


# --------------------------------------------------------------------------- поиск и документы

def chunk_texts(body: str) -> list[str]:
    """Фрагменты раздела для поискового индекса: вложение — отдельный фрагмент (длинный текст режется по абзацам)."""
    chunks: list[str] = []
    for e in real(parse_body(body)):
        if not e.text:
            continue
        label = ("Фото" if e.kind == "image" else "Текст") + f" «{e.name}»"
        head = label + (" (черновик распознавания)" if e.draft else "") + ": "
        buffer = ""
        for paragraph in [p.strip() for p in re.split(r"\n\s*\n|(?<=[.!?])\n", e.text) if p.strip()]:
            candidate = f"{buffer}\n{paragraph}".strip()
            if len(candidate) > 1000 and buffer:
                chunks.append(head + buffer)
                buffer = paragraph
            else:
                buffer = candidate
        if buffer:
            chunks.append(head + buffer[:1500])
    return chunks


MODEL_LIMIT = 4000


def model_context(meeting: knowledge.Meeting) -> str:
    """Текст вложений, дописанный к стенограмме при пересборке заметки; пустая строка, если вложений с текстом нет."""
    parts, used = [], 0
    for item in notes_for(meeting, 1500):
        piece = f"[{'Фото' if item['kind'] == 'image' else 'Текст'} «{item['name']}»]\n{item['text']}"
        if used + len(piece) > MODEL_LIMIT:
            break
        parts.append(piece)
        used += len(piece)
    if not parts:
        return ""
    return ("\n\n=== Материалы, приложенные к записи (не реплики участников; таймкодов у них нет) ===\n"
            + "\n\n".join(parts) + "\n")


def notes_for(meeting: knowledge.Meeting, limit: int = 1500) -> list[dict]:
    """Вложения с текстом для протокола и подготовки: название, текст (обрезанный) и признак черновика."""
    result = []
    for e in real(parse_content(meeting.content)):
        if e.text:
            result.append({"name": e.name, "kind": e.kind, "draft": e.draft,
                           "text": e.text if len(e.text) <= limit else e.text[:limit].rsplit(" ", 1)[0] + "…"})
    return result
