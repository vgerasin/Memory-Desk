"""Очередь обработки: запуск worker-процесса, статусы по файлам, этапы и человекочитаемые ошибки."""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from . import config, dictionary, storage

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4"}
# Аудио/видео-форматы, которые пайплайн не принимает: показываем их в очереди как ошибку с подсказкой.
UNSUPPORTED_EXTENSIONS = {".amr", ".wma", ".opus", ".aiff", ".aif", ".caf", ".3gp", ".webm", ".mkv", ".mov",
                          ".avi", ".wmv", ".m4b", ".ape"}


def _paths():
    return {
        "job": config.RUNTIME_DIR / "processing-job.json",
        "output": config.LOGS_DIR / "processing-worker.log",
        "events": config.LOGS_DIR / "processing.jsonl",
        "work": config.PROJECT_ROOT / "work",
        "script": config.PROJECT_ROOT / "meetings-process",
    }


# 12 этапов из дизайна. «Определение говорящих» — оценка локальной моделью по тексту (см. diarize.py).
STAGES = [
    ("checking", "Проверка файла", 2, "Система"),
    ("preparing_audio", "Подготовка аудио", 4, "Система"),
    ("transcribing", "Расшифровка", 46, "Whisper"),
    ("diarizing", "Определение говорящих", 6, "Ollama"),
    ("analyzing", "Анализ", 24, "Ollama"),
    ("writing_note", "Создание заметки", 4, "Система"),
    ("database", "Обновление базы", 3, "Система"),
    ("indexing", "Индексация", 5, "Система"),
    ("archiving", "Архивирование", 2, "Система"),
    ("verifying", "Проверка", 1, "Система"),
    ("publishing", "Публикация", 2, "Система"),
    ("completed", "Готово", 1, "Система"),
]
STAGE_INDEX = {key: i + 1 for i, (key, *_rest) in enumerate(STAGES)}
# Этапы, о которых сообщает worker; остальные выполняются мгновенно или на стороне сервера.
WORKER_STAGES = {"checking", "preparing_audio", "transcribing", "diarizing", "analyzing", "writing_note", "archiving", "completed"}
REALTIME_FACTOR = float(os.environ.get("MEMORY_DESK_WHISPER_SPEED", "10"))  # во сколько раз Whisper быстрее записи


def ru_plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    last = n % 10
    return one if last == 1 else few if 2 <= last <= 4 else many


# --------------------------------------------------------------------------- чтение состояния

def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_children: dict[int, subprocess.Popen] = {}
_children_lock = threading.Lock()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    with _children_lock:
        process = _children.get(pid)
    if process is not None:
        if process.poll() is None:
            return True
        with _children_lock:
            _children.pop(pid, None)
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # Завершённый, но не прочитанный потомок (zombie) тоже отвечает на kill(0).
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


def events(limit_bytes: int = 400_000) -> list[dict]:
    path = _paths()["events"]
    if not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit_bytes))
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = data.splitlines()
    if size > limit_bytes:
        lines = lines[1:]
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def discover(recordings_dir: str) -> list[Path]:
    folder = Path(recordings_dir).expanduser()
    if not folder.exists():
        return []
    return sorted(
        (item for item in folder.iterdir()
         if item.is_file() and item.suffix.casefold() in AUDIO_EXTENSIONS and not item.name.startswith(".")),
        key=lambda item: item.stat().st_mtime,
    )


def unsupported(recordings_dir: str) -> list[Path]:
    folder = Path(recordings_dir).expanduser()
    if not folder.exists():
        return []
    return sorted(
        (item for item in folder.iterdir()
         if item.is_file() and item.suffix.casefold() in UNSUPPORTED_EXTENSIONS and not item.name.startswith(".")),
        key=lambda item: item.stat().st_mtime,
    )


# --------------------------------------------------------------------------- хэши и дубликаты

_hash_cache: dict[tuple[str, int, int], str] = {}


def file_hash(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _hash_cache:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        _hash_cache[key] = digest.hexdigest()
    return _hash_cache[key]


def completed_hashes() -> set[str]:
    result = set()
    for state_path in _paths()["work"].glob("*/state.json"):
        state = _read_json(state_path)
        if state.get("status") == "completed" and state.get("source_hash"):
            note = Path(str(state.get("note", "")))
            if note.exists():
                result.add(str(state["source_hash"]))
    return result


def ignored_files() -> set[str]:
    try:
        return set(json.loads(storage.get_setting("ignored_files", "[]")))
    except json.JSONDecodeError:
        return set()


def ignore_file(name: str) -> None:
    storage.set_setting("ignored_files", json.dumps(sorted(ignored_files() | {name}), ensure_ascii=False))


def unignore_file(name: str) -> None:
    storage.set_setting("ignored_files", json.dumps(sorted(ignored_files() - {name}), ensure_ascii=False))


# --------------------------------------------------------------------------- ошибки

ERROR_RULES = [
    (r"Invalid data found|moov atom|could not find codec|ffprobe|Unsupported|не поддерж",
     "Формат не поддерживается", "formats"),
    (r"короче одной секунды", "Запись слишком короткая", None),
    (r"пустая или слишком короткая стенограмма|не найдено речи", "Речь в записи не найдена", None),
    (r"Ollama не отвечает|Connection refused", "Локальная модель Ollama недоступна", "system"),
    (r"некорректный JSON|неразборчивый ответ", "Модель вернула неразборчивый ответ", "log"),
    (r"Архив уже содержит файл", "Файл с таким именем уже есть в архиве", None),
    (r"Проверка готовой заметки", "Заметка создана некорректно", "log"),
    (r"No space left|Errno 28", "Не хватает места на диске", "system"),
    (r"Permission denied|Operation not permitted", "Нет доступа к файлу или папке", "settings"),
]


def humanize_error(detail: str, stage_label: str) -> dict:
    for pattern, title, action in ERROR_RULES:
        if re.search(pattern, detail or "", re.IGNORECASE):
            return {"title": title, "stage": stage_label, "action": action, "detail": detail}
    return {"title": "Не удалось обработать запись", "stage": stage_label, "action": "log", "detail": detail}


# --------------------------------------------------------------------------- длительность и оценка

_duration_cache: dict[tuple[str, int], float | None] = {}


def audio_duration(path: Path) -> float | None:
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if key in _duration_cache:
        return _duration_cache[key]
    value: float | None = None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        value = float(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        value = None
    _duration_cache[key] = value
    return value


def expected_seconds(stage: str, duration: float | None) -> float:
    d = duration or 600.0
    return {
        "checking": 2.0,
        "preparing_audio": 3.0 + d / 300,
        "transcribing": max(10.0, d / REALTIME_FACTOR),
        "diarizing": 15.0 + d / 30,
        "analyzing": 40.0 + d / 25,
        "writing_note": 2.0,
        "archiving": 2.0,
    }.get(stage, 1.0)


def estimate(stage: str, stage_started: datetime | None, duration: float | None, now: datetime) -> tuple[int, int | None]:
    """Процент по весам этапов и оценка оставшегося времени (секунды)."""
    index = STAGE_INDEX.get(stage, 1)
    base = sum(weight for _k, _l, weight, _r in STAGES[: index - 1])
    weight = STAGES[index - 1][2]
    elapsed = max(0.0, (now - stage_started).total_seconds()) if stage_started else 0.0
    expected = expected_seconds(stage, duration)
    fraction = min(0.97, elapsed / expected) if stage != "completed" else 1.0
    percent = int(round(base + weight * fraction))
    remaining = max(5.0, expected - elapsed)
    for key in ("transcribing", "diarizing", "analyzing", "writing_note", "archiving"):
        if STAGE_INDEX[key] > index:
            remaining += expected_seconds(key, duration)
    return min(99, percent) if stage != "completed" else 100, None if stage == "completed" else int(remaining)


def _parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- очередь

def build_queue(cfg: dict, now: datetime | None = None) -> dict:
    """Состояние очереди для интерфейса: по записи на файл."""
    now = now or datetime.now()
    paths = _paths()
    job = _read_json(paths["job"])
    alive = bool(job) and _pid_alive(int(job.get("pid", 0) or 0)) and job.get("state") == "running"
    all_events = events()
    job_id = job.get("id", "")
    ignored = ignored_files()
    result: list[dict] = []
    covered: set[str] = set()

    def last_event(source: str, within_job: bool) -> dict | None:
        for event in reversed(all_events):
            if event.get("source") == source and (not within_job or event.get("job_id") == job_id):
                return event
        return None

    def make(name: str, path: Path | None, state: str, event: dict | None, error: dict | None = None) -> dict:
        duration = audio_duration(path) if path and path.exists() else None
        stage = (event or {}).get("stage") or "checking"
        if stage in {"started", "progress"}:
            stage = "checking"
        stage = stage if stage in STAGE_INDEX else "checking"
        if stage == "error":
            stage = "checking"
        item = {
            "id": f"{job_id}:{name}" if state == "running" else name,
            "file": name, "duration": duration, "state": state, "stage": stage,
            "stage_index": STAGE_INDEX[stage], "stage_label": STAGES[STAGE_INDEX[stage] - 1][1],
            "percent": 0, "started_at": job.get("started_at") if state == "running" else None,
            "eta": None, "last_message": (event or {}).get("detail", ""), "runner": STAGES[STAGE_INDEX[stage] - 1][3],
            "error": error,
        }
        if state == "running":
            started = _parse_time((event or {}).get("created_at")) if event else None
            percent, eta = estimate(stage, started, duration, now)
            item.update(percent=percent, eta=eta)
        return item

    if job:
        for name in job.get("files", []):
            events_for = [e for e in all_events if e.get("source") == name and e.get("job_id") == job_id]
            latest = events_for[-1] if events_for else None
            path = Path(cfg["recordings_dir"]).expanduser() / name
            kind = (latest or {}).get("event")
            if kind in {"completed", "duplicate_skipped"}:
                covered.add(name)
                continue
            if kind == "error":
                covered.add(name)
                last_stage = next((e.get("stage") for e in reversed(events_for)
                                   if e.get("stage") in STAGE_INDEX and e.get("stage") != "error"), "checking")
                label = STAGES[STAGE_INDEX.get(last_stage, 1) - 1][1]
                result.append(make(name, path, "failed", {**latest, "stage": last_stage},
                                   humanize_error(latest.get("detail", ""), label)))
            elif alive:
                covered.add(name)
                started_any = latest is not None
                result.append(make(name, path, "running" if started_any else "queued", latest))
            elif path.exists():
                covered.add(name)
                cancelled = job.get("state") == "cancelled"
                label = STAGES[STAGE_INDEX.get((latest or {}).get("stage"), 1) - 1][1] if latest else STAGES[0][1]
                result.append(make(name, path, "failed", latest, {
                    "title": "Обработка отменена" if cancelled else "Обработка прервана",
                    "stage": label, "action": "retry",
                    "detail": "Задание остановлено до завершения. Повторите запуск — готовые этапы сохранены."}))
    # Файлы в папке, не охваченные текущим заданием
    hashes: set[str] | None = None
    for path in discover(cfg["recordings_dir"]):
        if path.name in covered or path.name in ignored:
            continue
        latest = last_event(path.name, within_job=False)
        if latest and latest.get("event") == "duplicate_skipped":
            continue
        if latest and latest.get("event") == "error":
            created = _parse_time(latest.get("created_at"))
            if created and created.timestamp() >= path.stat().st_mtime:
                label = STAGES[STAGE_INDEX.get(latest.get("stage"), 1) - 1][1]
                result.append(make(path.name, path, "failed", latest, humanize_error(latest.get("detail", ""), label)))
                continue
        if hashes is None:
            hashes = completed_hashes()
        try:
            if file_hash(path) in hashes:
                continue
        except OSError:
            continue
        result.append(make(path.name, path, "queued", None))
    for path in unsupported(cfg["recordings_dir"]):
        if path.name in ignored:
            continue
        result.append(make(path.name, path, "failed", {"stage": "checking", "detail": "Unsupported format"}, {
            "title": "Формат не поддерживается", "stage": STAGES[0][1], "action": "formats",
            "detail": f"Расширение {path.suffix} не поддерживается. Допустимы: MP3, M4A, WAV, AAC, OGG, FLAC, MP4."}))
    order = {"running": 0, "failed": 1, "queued": 2}
    result.sort(key=lambda item: order.get(item["state"], 3))
    running = next((item for item in result if item["state"] == "running"), None)
    return {"jobs": result, "running": running, "active": alive}


def queue_fingerprint(queue: dict) -> str:
    stable = [{k: v for k, v in item.items() if k not in {"percent", "eta"}} for item in queue["jobs"]]
    return hashlib.md5(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------- запуск и отмена

def is_running() -> bool:
    job = _read_json(_paths()["job"])
    return bool(job) and job.get("state") == "running" and _pid_alive(int(job.get("pid", 0) or 0))


def eligible_files(cfg: dict) -> tuple[list[Path], int]:
    """Файлы, которые стоит обработать, и число пропущенных дубликатов (по SHA-256)."""
    ignored = ignored_files()
    hashes = completed_hashes()
    files, duplicates = [], 0
    for path in discover(cfg["recordings_dir"]):
        if path.name in ignored:
            continue
        try:
            if file_hash(path) in hashes:
                duplicates += 1
                continue
        except OSError:
            continue
        files.append(path)
    return files, duplicates


def start(cfg: dict, files: list[Path] | None = None) -> dict:
    if is_running():
        return {"created": False, "count": 0, "duplicates": 0, "message": "Обработка уже идёт"}
    duplicates = 0
    if files is None:
        files, duplicates = eligible_files(cfg)
    if not files:
        message = "Новых файлов нет · дубликаты пропущены" if duplicates else "Новых файлов нет"
        return {"created": False, "count": 0, "duplicates": duplicates, "message": message}
    paths = _paths()
    script = paths["script"]
    if not script.exists():
        return {"created": False, "count": 0, "duplicates": duplicates,
                "message": "Не найден скрипт обработки meetings-process"}
    config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    command = [str(script), *[str(p) for p in files], "--vault", cfg["obsidian_dir"],
               "--archive-dir", cfg["archive_dir"], "--retention-days", str(cfg["retention_days"]),
               "--job-id", job_id]
    output = paths["output"].open("a", encoding="utf-8")
    try:
        env = {**os.environ, "PLAUD_DIARIZE": "1" if cfg.get("diarize", True) else "0",
               "PLAUD_VOICE": "1" if cfg.get("voice_enabled") else "0",
               "PLAUD_DICTIONARY": str(dictionary.path())}
        process = subprocess.Popen(command, cwd=config.PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True, env=env)
    finally:
        output.close()
    with _children_lock:
        _children[process.pid] = process
    record = {"id": job_id, "pid": process.pid, "state": "running",
              "started_at": datetime.now().isoformat(timespec="seconds"),
              "files": [p.name for p in files], "count": len(files)}
    paths["job"].write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n = len(files)
    if ru_plural(n, "one", "few", "many") == "one":
        message = f"Найден {n} новый файл · добавлен в очередь"
    else:
        noun = ru_plural(n, "новый файл", "новых файла", "новых файлов")
        message = f"Найдено {n} {noun} · добавлено в очередь"
    return {"created": True, "count": n, "duplicates": duplicates, "message": message, "job": record}


def cancel() -> bool:
    paths = _paths()
    job = _read_json(paths["job"])
    pid = int(job.get("pid", 0) or 0)
    if not _pid_alive(pid):
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    job["state"] = "cancelled"
    job["cancelled_at"] = datetime.now().isoformat(timespec="seconds")
    paths["job"].write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def log_for(name: str, limit: int = 60) -> list[dict]:
    relevant = [e for e in events() if e.get("source") == name]
    return relevant[-limit:]


def worker_log_tail(lines: int = 40) -> str:
    path = _paths()["output"]
    if not path.exists():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 20_000))
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])
