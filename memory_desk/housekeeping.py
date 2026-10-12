"""Хранилище: сколько что занимает и что можно освободить. Заметки Obsidian и база никогда не удаляются отсюда."""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import config, storage

AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4"}


def _walk_files(root: Path):
    if not root.exists():
        return
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = Path(base) / name
            if not path.is_symlink():
                yield path


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _tree(root: Path) -> tuple[int, int]:
    total = count = 0
    for path in _walk_files(root):
        total += _size(path)
        count += 1
    return total, count


def _work_wavs() -> list[Path]:
    root = config.PROJECT_ROOT / "work"
    return [p for p in _walk_files(root) if p.name == "audio-16k.wav"]


def _work_results() -> list[Path]:
    root = config.PROJECT_ROOT / "work"
    return [p for p in _walk_files(root) if p.name != "audio-16k.wav"]


def _archive_audio(cfg: dict) -> list[Path]:
    return [p for p in _walk_files(Path(cfg["archive_dir"]).expanduser()) if p.suffix.lower() in AUDIO]


def _logs() -> list[Path]:
    return [p for p in _walk_files(config.LOGS_DIR) if p.suffix == ".log"]


def usage(cfg: dict) -> dict:
    wavs = _work_wavs()
    results = _work_results()
    audio = _archive_audio(cfg)
    logs = _logs()
    backups = storage.list_backups()
    vault = Path(cfg["obsidian_dir"]).expanduser()
    vault_bytes, vault_count = _tree(vault)
    models = _tree(config.MODEL_CACHE)[0]
    ollama = _tree(config.PROJECT_ROOT / "ollama-models")[0]
    index = _tree(config.DATA_DIR / "qdrant")[0]
    db_bytes = sum(_size(p) for p in config.DATA_DIR.glob("meetings.db*")) if config.DATA_DIR.exists() else 0
    oldest = min((p.stat().st_mtime for p in audio), default=None)
    categories = [
        {"key": "work_audio", "title": "Промежуточное аудио обработки", "bytes": sum(_size(p) for p in wavs), "count": len(wavs),
         "hint": "Копия записи в 16 кГц для распознавания (около 110 МБ на час). После обработки не нужна.",
         "clean": "Удалить", "days": False, "danger": False},
        {"key": "work_results", "title": "Промежуточные результаты", "bytes": sum(_size(p) for p in results), "count": len(results),
         "hint": "Сырая расшифровка и анализ в папке work. Заметки в Obsidian от них не зависят; для повторного анализа записи потребуется расшифровка заново.",
         "clean": "Удалить", "days": False, "danger": True},
        {"key": "archive_audio", "title": "Исходное аудио в архиве", "bytes": sum(_size(p) for p in audio), "count": len(audio),
         "hint": "Обработанные записи ждут здесь срока хранения (сейчас " + str(cfg.get("retention_days", 14)) + " дн.). Текст, заметка и поиск остаются, но запись нельзя будет прослушать.",
         "clean": "Удалить старше…", "days": True, "danger": True, "oldest": oldest},
        {"key": "backups", "title": "Резервные копии базы", "bytes": sum(_size(p) for p in backups), "count": len(backups),
         "hint": "Остаются три последние копии.", "clean": "Оставить 3", "days": False, "danger": False},
        {"key": "logs", "title": "Журналы работы", "bytes": sum(_size(p) for p in logs), "count": len(logs),
         "hint": "Текстовые журналы сервера и обработки. История обработки (processing.jsonl) не затрагивается.",
         "clean": "Очистить", "days": False, "danger": False},
    ]
    info = [
        {"key": "notes", "title": "Заметки и стенограммы в Obsidian", "bytes": vault_bytes, "count": vault_count},
        {"key": "database", "title": "База данных", "bytes": db_bytes, "count": None},
        {"key": "index", "title": "Поисковый индекс", "bytes": index, "count": None},
        {"key": "models", "title": "Модели поиска и распознавания", "bytes": models, "count": None},
        {"key": "ollama", "title": "Модель Ollama", "bytes": ollama, "count": None},
    ]
    return {"categories": categories, "info": info,
            "total": sum(c["bytes"] for c in categories) + sum(i["bytes"] for i in info)}


def clean(cfg: dict, category: str, days: int | None = None) -> dict:
    freed = removed = 0

    def drop(paths: list[Path]) -> None:
        nonlocal freed, removed
        for path in paths:
            size = _size(path)
            try:
                path.unlink()
            except OSError:
                continue
            freed += size
            removed += 1

    if category == "work_audio":
        drop(_work_wavs())
    elif category == "work_results":
        drop(_work_results())
        root = config.PROJECT_ROOT / "work"
        for folder in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True) if root.exists() else []:
            try:
                folder.rmdir()
            except OSError:
                pass
    elif category == "archive_audio":
        limit = time.time() - max(0, int(days if days is not None else cfg.get("retention_days", 14))) * 86400
        drop([p for p in _archive_audio(cfg) if p.stat().st_mtime <= limit])
        archive = Path(cfg["archive_dir"]).expanduser()
        for folder in sorted((p for p in archive.rglob("*") if p.is_dir()), reverse=True) if archive.exists() else []:
            try:
                folder.rmdir()
            except OSError:
                pass
    elif category == "backups":
        drop(storage.list_backups()[3:])
    elif category == "logs":
        for path in _logs():
            size = _size(path)
            try:
                path.write_text("", encoding="utf-8")
            except OSError:
                continue
            freed += size
            removed += 1
    else:
        raise ValueError("Неизвестная категория")
    return {"freed": freed, "removed": removed}
