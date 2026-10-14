"""Мастер установки: что нужно кабинету для работы, чего не хватает и как это докачать, не выходя из кабинета.

Один и тот же мастер работает и в браузере (после `install.command`), и в окне приложения для Mac. Он ничего не ставит
молча: каждая загрузка запускается кнопкой, размер указан заранее, ход виден, остановить можно в любой момент.
Сначала скрипт установки (или приложение) готовит минимум: Python-окружение и пакеты кабинета. Остальное, то есть
`ffmpeg`, Ollama, модели распознавания речи, анализа и поиска, голоса, текст на фото, доступы, делает мастер.

Модули проверяются через `find_spec` (без импорта: так мастер не тянет за собой тяжёлые библиотеки), модели — по папкам
кеша. Загрузки с Hugging Face идут отдельным процессом (сервер работает в режиме `HF_HUB_OFFLINE=1`, поэтому ему самому
сеть к Hugging Face закрыта), модель Ollama — потоком через её HTTP API, команды вроде `brew install ffmpeg` — из
фиксированного списка, пользовательский текст в команды не попадает.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
import urllib.error
import urllib.request
from pathlib import Path

from . import asr, config, llm

CORE_MODULES = ["fastapi", "uvicorn", "qdrant_client", "sentence_transformers", "mlx_whisper", "icalendar",
                "recurring_ical_events", "docx", "reportlab", "PIL", "numpy", "scipy"]
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_MB = 470
# Примерные размеры (МБ): для подписи и полосы хода; точный размер известен только после начала загрузки.
WHISPER_MB = {"mlx-community/whisper-large-v3-turbo": 1600, "mlx-community/whisper-large-v3-mlx": 3100, "mlx-community/whisper-medium-mlx": 1500}
LLM_MB = {"qwen3:4b": 2500, "qwen3:8b": 5200}
NEED_FREE_MARGIN_GB = 3
BREW_DIRS = ["/opt/homebrew/bin", "/usr/local/bin"]
LOG_LINES = 40


class SetupError(RuntimeError):
    """Сообщение для человека: что не получилось и что сделать."""


# --------------------------------------------------------------------------- проверки

def _path() -> str:
    return os.pathsep.join([*BREW_DIRS, os.environ.get("PATH", "")])


def which(name: str) -> str | None:
    return shutil.which(name, path=_path())


def have_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def hf_cached(repo: str, cache_dir: Path) -> bool:
    """Модель есть в кеше целиком: каталог снимков не пуст и недокачанных файлов нет."""
    base = cache_dir / ("models--" + repo.replace("/", "--"))
    snapshots = base / "snapshots"
    try:
        has_snapshot = snapshots.is_dir() and any(snapshots.iterdir())
        partial = any((base / "blobs").glob("*.incomplete")) if (base / "blobs").is_dir() else False
    except OSError:
        return False
    return has_snapshot and not partial


def dir_bytes(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def whisper_repo(cfg: dict) -> str:
    return asr.resolve_model(cfg.get("asr_model") or asr.DEFAULT_MODEL)


def whisper_cache() -> Path:
    return config.MODEL_CACHE / "hub"


def free_gb(path: Path | None = None) -> float:
    target = path or config.PROJECT_ROOT
    try:
        return round(shutil.disk_usage(target).free / 1024 ** 3, 1)
    except OSError:
        return 0.0


def _component(cid: str, title: str, group: str, note: str, state: str, *, required: bool = True, detail: str = "",
               mb: int | None = None, action: str = "", label: str = "") -> dict:
    return {"id": cid, "title": title, "group": group, "note": note, "state": state, "required": required, "detail": detail,
            "mb": mb, "action": action, "actionLabel": label}


def check(cfg: dict) -> list[dict]:
    """Состояние всех компонентов (без загрузок и запуска процессов, кроме короткого запроса к Ollama)."""
    out: list[dict] = []
    missing = [m for m in CORE_MODULES if not have_module(m)]
    out.append(_component("packages", "Python-пакеты кабинета", "system", "Поиск, распознавание речи, документы, календарь.",
                          "ok" if not missing else "missing", detail=("не хватает: " + ", ".join(missing)) if missing else "",
                          action="" if not missing else "run", label="Доустановить"))
    ffmpeg = which("ffmpeg")
    out.append(_component("ffmpeg", "ffmpeg", "system", "Приводит звук к нужному виду перед распознаванием.", "ok" if ffmpeg else "missing",
                          detail=ffmpeg or "", action="" if ffmpeg else ("run" if which("brew") else "hint"),
                          label="Установить (brew)" if which("brew") else ""))
    ollama = which("ollama")
    out.append(_component("ollama_bin", "Ollama", "system", "Локальная программа, в которой работает языковая модель.", "ok" if ollama else "missing",
                          detail=ollama or "", action="" if ollama else ("run" if which("brew") else "hint"),
                          label="Установить (brew)" if which("brew") else ""))
    installed = llm.list_models(config.OLLAMA_URL, timeout=1.0)
    out.append(_component("ollama_server", "Ollama запущена", "system", f"Кабинет обращается к ней по адресу {config.OLLAMA_URL}.",
                          "ok" if installed is not None else "missing", detail="" if installed is not None else "не отвечает",
                          action="run" if installed is None and ollama else "", label="Запустить"))

    model = config.llm_model(cfg)
    llm_ok = installed is not None and llm.has_model(installed, model)
    out.append(_component("llm", f"Модель анализа {model}", "models", "Пишет заметку по стенограмме, отвечает в чате.",
                          "ok" if llm_ok else "missing", mb=LLM_MB.get(model),
                          detail="" if llm_ok else ("Ollama не запущена" if installed is None else "не скачана"),
                          action="" if llm_ok else ("run" if installed is not None else ""), label="Скачать"))
    repo = whisper_repo(cfg)
    asr_ok = hf_cached(repo, whisper_cache())
    out.append(_component("whisper", "Распознавание речи", "models", f"{repo.split('/')[-1]}: превращает аудио в текст.",
                          "ok" if asr_ok else "missing", mb=WHISPER_MB.get(repo), action="" if asr_ok else "run", label="Скачать"))
    emb_ok = hf_cached(EMBEDDING_MODEL, config.MODEL_CACHE)
    out.append(_component("embedding", "Модель поиска", "models", "Нужна для поиска по записям и для чата.",
                          "ok" if emb_ok else "missing", mb=EMBEDDING_MB, action="" if emb_ok else "run", label="Скачать"))

    from . import voice
    voice_ok = voice.model_ready()
    out.append(_component("voice", "Голоса говорящих", "extras", "Запоминает голоса, чтобы подписывать реплики именами.",
                          "ok" if voice_ok else "missing", required=False, mb=100, action="" if voice_ok else "run", label="Скачать"))
    vision = have_module("Vision")
    out.append(_component("vision", "Текст на фото", "extras", "Распознаёт текст на снимках доски и бумаг.",
                          "ok" if vision else "missing", required=False, action="" if vision else "run", label="Установить"))
    eventkit = have_module("EventKit")
    out.append(_component("eventkit", "Календарь и Напоминания", "extras", "Подготовка к встречам и задачи в «Напоминаниях».",
                          "ok" if eventkit else "missing", required=False, action="" if eventkit else "run", label="Установить"))
    return out


def folders(cfg: dict) -> dict:
    rec, vault = Path(str(cfg.get("recordings_dir", ""))).expanduser(), Path(str(cfg.get("obsidian_dir", ""))).expanduser()
    return {"recordingsDir": str(cfg.get("recordings_dir", "")), "obsidianDir": str(cfg.get("obsidian_dir", "")),
            "recordingsOk": rec.is_dir(), "obsidianOk": vault.is_dir(), "canPick": can_pick()}


def places(cfg: dict) -> list[dict]:
    """Где что лежит на диске: человек должен видеть, куда встала программа, данные и модели."""
    rows = [
        ("program", "Программа", "Сама программа и её окружение Python (папка с файлом meeting-assistant)", config.PROJECT_ROOT),
        ("data", "Данные кабинета", "База, настройки, индекс, резервные копии", config.DATA_DIR),
        ("models", "Модели", "Модели поиска и распознавания речи (кеш Hugging Face)", config.MODEL_CACHE),
        ("recordings", "Записи", "Сюда кладите аудиофайлы", Path(str(cfg.get("recordings_dir", ""))).expanduser()),
        ("notes", "Заметки", "Готовые заметки, протоколы и документы (Obsidian)", Path(str(cfg.get("obsidian_dir", ""))).expanduser()),
        ("logs", "Журналы", "Если что-то пошло не так, смотрите сюда", config.LOGS_DIR),
    ]
    return [{"key": k, "title": t, "note": n, "path": str(p), "exists": p.exists()} for k, t, n, p in rows]


def reveal(key: str, cfg: dict) -> str:
    """Открывает папку в Finder. Только из фиксированного списка мест."""
    found = next((row for row in places(cfg) if row["key"] == key), None)
    if found is None:
        raise ValueError("Неизвестное место")
    if sys.platform != "darwin" or not which("open"):
        raise RuntimeError("Открыть папку можно только на Mac: путь указан выше")
    if not found["exists"]:
        raise RuntimeError("Этой папки ещё нет")
    subprocess.run(["open", found["path"]], check=False, timeout=10)
    return found["path"]


def status(cfg: dict, meetings: int = 0) -> dict:
    items = check(cfg)
    for item in items:
        failed = runner.last.get(item["id"])
        if item["state"] != "ok" and failed and failed["state"] == "error":
            item["state"], item["detail"] = "error", failed["message"]
    need_mb = sum(c["mb"] or 0 for c in items if c["state"] != "ok" and c["group"] == "models")
    free = free_gb()
    required_ok = all(c["state"] == "ok" for c in items if c["required"])
    return {"components": items, "folders": folders(cfg), "disk": {"freeGb": free, "needGb": round(need_mb / 1024 + (NEED_FREE_MARGIN_GB if need_mb else 0), 1)},
            "places": places(cfg), "ready": required_ok, "done": bool(cfg.get("setup_done")), "fresh": meetings == 0, "brew": bool(which("brew")),
            "needsWizard": (not cfg.get("setup_done")) and (not required_ok or meetings == 0), "job": runner.view()}


# --------------------------------------------------------------------------- выбор папки

PICK_PROMPTS = {"recordings": "Папка с записями", "vault": "Папка заметок в Obsidian"}
_PICK_SCRIPT = """on run argv
  set promptText to item 1 of argv
  set startPath to item 2 of argv
  if startPath is "" then
    return POSIX path of (choose folder with prompt promptText)
  end if
  return POSIX path of (choose folder with prompt promptText default location (POSIX file startPath))
end run"""


def can_pick() -> bool:
    return sys.platform == "darwin" and which("osascript") is not None


def pick_folder(kind: str, start: str = "") -> str:
    """Системное окно выбора папки macOS (в нём есть кнопка «Новая папка»). Пустая строка — окно закрыли без выбора."""
    if kind not in PICK_PROMPTS:
        raise ValueError("Неизвестная папка")
    if not can_pick():
        raise RuntimeError("Окно выбора папки есть только в macOS: впишите путь вручную")
    folder = Path(start).expanduser() if start.strip() else None
    while folder is not None and not folder.is_dir() and folder != folder.parent:
        folder = folder.parent
    begin = str(folder) if folder is not None and folder.is_dir() else ""
    try:
        done = subprocess.run(["osascript", "-e", _PICK_SCRIPT, PICK_PROMPTS[kind], begin], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return ""
    if done.returncode != 0:
        if "-128" in done.stderr:  # «Отмена»
            return ""
        raise RuntimeError("Не удалось открыть окно выбора папки")
    return done.stdout.strip().rstrip("/") or ""


# --------------------------------------------------------------------------- загрузки

class Job:
    def __init__(self, cid: str, title: str) -> None:
        self.id, self.title = cid, title
        self.state, self.message = "running", ""
        self.done, self.total = 0, 0
        self.bytes = False                       # done/total — байты (тогда показываем скорость), иначе условные доли
        self.log: list[str] = []
        self.cancelled = False
        self.proc: subprocess.Popen | None = None
        self.started = time.time()
        self.stages: list[str] = []              # названия этапов, например «Подготовка», «Загрузка», «Установка»
        self.stage = 0
        self.note = ""                           # что делается прямо сейчас: имя пакета, файла
        self._samples: deque[tuple[float, int]] = deque(maxlen=40)

    def line(self, text: str) -> None:
        text = text.rstrip()
        if text:
            self.log = (self.log + [text[:300]])[-LOG_LINES:]
            self.message = text[:160]

    def set_stages(self, names: list[str]) -> None:
        self.stages, self.stage = list(names), 0

    def go(self, stage: int, note: str | None = None) -> None:
        """Переход к этапу (назад не идём: вывод команд бывает неупорядоченным)."""
        if self.stages:
            self.stage = max(self.stage, min(stage, len(self.stages) - 1))
        if note is not None:
            self.note = note[:120]

    def rate(self) -> float | None:
        """Скорость за последние секунды (единиц done в секунду); None, пока мало данных."""
        now = time.time()
        if self.total and self.state == "running":
            self._samples.append((now, self.done))
        recent = [(t, d) for t, d in self._samples if now - t <= 20]
        if len(recent) < 2 or recent[-1][0] - recent[0][0] < 2:
            return None
        rate = (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
        return rate if rate > 0 else None

    def view(self) -> dict:
        percent = round(100 * self.done / self.total) if self.total else None
        rate = self.rate()
        eta = round((self.total - self.done) / rate) if rate and self.total > self.done and self.state == "running" else None
        return {"id": self.id, "title": self.title, "state": self.state, "message": self.message, "done": self.done, "total": self.total,
                "percent": min(percent, 99) if percent is not None and self.state == "running" else percent,
                "seconds": round(time.time() - self.started), "log": self.log[-8:],
                "stages": self.stages, "stage": self.stage, "note": self.note, "bytes": self.bytes,
                "speed": round(rate) if rate and self.bytes else None, "eta": eta}


class Runner:
    """Одна загрузка за раз: диск и сеть общие, а два прогресса подряд человеку не нужны."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.job: Job | None = None
        self.last: dict[str, dict] = {}       # id компонента -> итог последней попытки (ошибка остаётся видна)

    def view(self) -> dict | None:
        with self._lock:
            return self.job.view() if self.job else None

    def start(self, cid: str, cfg: dict) -> bool:
        with self._lock:
            if self.job and self.job.state == "running":
                return False
            handler = HANDLERS.get(cid)
            if handler is None:
                raise SetupError("Такого компонента нет")
            self.job = Job(cid, TITLES.get(cid, cid))
            job = self.job
        threading.Thread(target=self._run, args=(handler, job, dict(cfg)), name=f"setup-{cid}", daemon=True).start()
        return True

    def _run(self, handler, job: Job, cfg: dict) -> None:
        try:
            handler(job, cfg)
            job.state = "cancelled" if job.cancelled else "done"
            job.message = "Остановлено" if job.cancelled else "Готово"
        except SetupError as exc:
            job.state, job.message = "error", str(exc)
        except Exception as exc:  # noqa: BLE001 — любая неожиданность должна стать понятным текстом, а не пропасть в потоке
            job.state, job.message = "error", f"Непредвиденная ошибка: {exc}"
        with self._lock:
            self.last[job.id] = job.view()

    def cancel(self) -> bool:
        with self._lock:
            job = self.job
        if not job or job.state != "running":
            return False
        job.cancelled = True
        proc = job.proc
        if proc and proc.poll() is None:
            proc.terminate()
        return True

    def reset(self) -> None:
        with self._lock:
            self.job, self.last = None, {}


runner = Runner()


def stream_command(job: Job, argv: list[str], env: dict | None = None, poll=None, label: str = "", on_line=None) -> None:
    """Запускает команду, строки вывода идут в журнал задачи; `poll()` вызывается раз в полсекунды (для хода загрузки)."""
    merged = {**os.environ, "PATH": _path(), **(env or {})}
    try:
        job.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=merged, bufsize=1)
    except OSError as exc:
        raise SetupError(f"Не удалось запустить {label or argv[0]}: {exc}") from exc
    def read() -> None:
        for line in job.proc.stdout:                                  # type: ignore[union-attr]
            job.line(line)
            if on_line:
                try:
                    on_line(line.strip())
                except Exception:                                      # разбор вывода не должен ронять установку
                    pass
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    while job.proc.poll() is None:
        if poll:
            poll()
        time.sleep(0.5)
    reader.join(timeout=2)
    if job.cancelled:
        return
    if job.proc.returncode != 0:
        errors = [line for line in job.log if line.startswith(("ERROR", "Error", "error"))]
        tail = errors[-1] if errors else " | ".join(job.log[-3:])
        raise SetupError(f"{label or argv[0]} завершилась с ошибкой ({job.proc.returncode}): {tail}"[:400])


def _hf_download(job: Job, code: str, repo: str, cache_dir: Path, expected_mb: int | None) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    folder = cache_dir / ("models--" + repo.replace("/", "--"))
    job.total = (expected_mb or 0) * 1024 * 1024
    job.bytes = True
    job.set_stages(["Загрузка", "Проверка"])
    job.go(0, repo)
    env = {"HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0", "HF_HUB_DISABLE_PROGRESS_BARS": "1", "HF_HOME": str(config.MODEL_CACHE),
           "SETUP_REPO": repo, "SETUP_CACHE": str(cache_dir)}

    def poll() -> None:
        job.done = dir_bytes(folder / "blobs")
        if job.total and job.done > job.total:
            job.total = int(job.done * 1.05)         # оценка размера занижена: шкала не должна упираться в край
    stream_command(job, [sys.executable, "-c", code], env, poll, "Загрузка модели")
    job.go(1, "Проверяю, что модель загружена целиком")
    job.done = job.total = max(job.done, 1)
    if not job.cancelled and not hf_cached(repo, cache_dir):
        raise SetupError("Загрузка закончилась, но модели в кеше нет: проверьте интернет и повторите")


def _download_whisper(job: Job, cfg: dict) -> None:
    repo = whisper_repo(cfg)
    _hf_download(job, "import os\nfrom huggingface_hub import snapshot_download\nsnapshot_download(os.environ['SETUP_REPO'], cache_dir=os.environ['SETUP_CACHE'])",
                 repo, whisper_cache(), WHISPER_MB.get(repo))


def _download_embedding(job: Job, cfg: dict) -> None:
    _hf_download(job, "import os\nfrom sentence_transformers import SentenceTransformer\nSentenceTransformer(os.environ['SETUP_REPO'], cache_folder=os.environ['SETUP_CACHE'])",
                 EMBEDDING_MODEL, config.MODEL_CACHE, EMBEDDING_MB)


def _pull_llm(job: Job, cfg: dict) -> None:
    model = config.llm_model(cfg)
    if llm.list_models(config.OLLAMA_URL, timeout=2) is None:
        raise SetupError("Ollama не запущена. Сначала запустите её (кнопка выше) и повторите.")
    request = urllib.request.Request(f"{config.OLLAMA_URL}/api/pull", data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw in response:
                if job.cancelled:
                    return                  # закрытие соединения останавливает загрузку, Ollama продолжит с этого места
                try:
                    row = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                if row.get("error"):
                    raise SetupError(f"Ollama: {row['error']}")
                status = str(row.get("status", ""))
                job.line(status)
                job.bytes = True
                if not job.stages:
                    job.set_stages(["Подготовка", "Загрузка", "Проверка"])
                if status.startswith("pulling manifest"):
                    job.go(0, "Запрашиваю список файлов модели")
                elif status.startswith("pulling"):
                    job.go(1, f"Файл {status.split()[-1][:12]}")
                elif status.startswith(("verifying", "writing", "removing", "success")):
                    job.go(2, status)
                if row.get("total"):
                    job.total, job.done = int(row["total"]), int(row.get("completed") or 0)
    except urllib.error.HTTPError as exc:
        raise SetupError(f"Ollama ответила ошибкой {exc.code}: имя модели «{model}» не найдено в каталоге?") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SetupError(f"Связь с Ollama оборвалась: {exc}") from exc
    installed = llm.list_models(config.OLLAMA_URL, timeout=3)
    if not (installed and llm.has_model(installed, model)):
        raise SetupError("Загрузка закончилась, но модели в Ollama нет: повторите")


def _start_ollama(job: Job, cfg: dict) -> None:
    binary = which("ollama")
    if not binary:
        raise SetupError("Ollama не установлена")
    host = config.OLLAMA_URL.split("://", 1)[-1]
    if not host.startswith(("127.0.0.1", "localhost")):
        raise SetupError(f"Адрес Ollama {config.OLLAMA_URL} внешний: запустите её там сами")
    env = {**os.environ, "PATH": _path(), "OLLAMA_HOST": host, "OLLAMA_MODELS": str(config.PROJECT_ROOT / "ollama-models")}
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log = (config.LOGS_DIR / "ollama.log").open("a", encoding="utf-8")
    subprocess.Popen([binary, "serve"], env=env, stdout=log, stderr=log, start_new_session=True)
    for _ in range(30):
        if llm.list_models(config.OLLAMA_URL, timeout=1) is not None:
            return
        time.sleep(0.5)
    raise SetupError(f"Ollama не запустилась за 15 секунд. Журнал: {config.LOGS_DIR / 'ollama.log'}")


PIP_STAGES = ["Подготовка", "Загрузка пакетов", "Установка"]
BREW_STAGES = ["Подготовка", "Загрузка", "Установка"]
_PIP_COLLECT = re.compile(r"^(?:Collecting|Obtaining)\s+([^\s;=<>!~\[]+)")
_PIP_DOWNLOAD = re.compile(r"^Downloading\s+(\S+)\s+\(([^)]+)\)")
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d)?)%\s*$")


def pip_line_parser(job: Job):
    """Разбирает вывод pip: сколько пакетов уже найдено, какой качается сейчас, когда пошла установка."""
    state = {"found": 0}

    def parse(line: str) -> None:
        if (m := _PIP_COLLECT.match(line)):
            state["found"] += 1
            job.go(1, f"{m.group(1)} · пакетов найдено: {state['found']}")
        elif (m := _PIP_DOWNLOAD.match(line)):
            job.go(1, f"{m.group(1)} · {m.group(2)}")
        elif line.startswith(("Building wheel", "Preparing metadata", "Getting requirements")):
            job.go(1, f"Подготовка пакета: {line[:80]}")
        elif line.startswith("Installing collected packages"):
            count = len([p for p in line.split(":", 1)[-1].split(",") if p.strip()])
            job.go(2, f"Ставлю {count} пакетов: это занимает до нескольких минут")
        elif line.startswith("Successfully installed"):
            job.go(2, "Готово")
    job.set_stages(PIP_STAGES)
    return parse


def brew_line_parser(job: Job):
    def parse(line: str) -> None:
        if line.startswith("==> Fetching") or line.startswith("==> Downloading"):
            job.go(1, line.lstrip("= >")[:100])
        elif (m := _PERCENT.search(line)) and "#" in line:
            job.go(1)
            job.total, job.done = 1000, min(1000, int(float(m.group(1)) * 10))
        elif line.startswith(("==> Pouring", "==> Installing", "==> Moving", "==> Linking", "==> Summary")):
            job.go(2, line.lstrip("= >")[:100])
            job.total = job.done = 0
    job.set_stages(BREW_STAGES)
    return parse


def _brew(package: str):
    def run(job: Job, cfg: dict) -> None:
        brew = which("brew")
        if not brew:
            raise SetupError("Нет Homebrew: поставьте его с https://brew.sh и повторите")
        stream_command(job, [brew, "install", package], {"HOMEBREW_NO_AUTO_UPDATE": "1"}, label=f"brew install {package}", on_line=brew_line_parser(job))
    return run


def _pip(*packages: str):
    def run(job: Job, cfg: dict) -> None:
        argv = [sys.executable, "-m", "pip", "install", *(packages or ("-r", str(config.PROJECT_ROOT / "requirements.txt")))]
        stream_command(job, argv, label="pip", on_line=pip_line_parser(job))
        importlib.invalidate_caches()            # только что поставленный модуль (Vision, EventKit) должен подхватиться без перезапуска
    return run


def _voice(job: Job, cfg: dict) -> None:
    from . import voice
    voice.start_download()
    while True:
        state = voice.download_status()
        job.done, job.total = int(state.get("done") or 0), int(state.get("total") or 0)
        if state.get("message"):
            job.line(str(state["message"]))
        if state.get("state") != "running":
            break
        if job.cancelled:
            return
        time.sleep(0.5)
    if state.get("state") == "error":
        raise SetupError(str(state.get("message") or "Не удалось подготовить модель голосов"))


HANDLERS = {
    "packages": _pip(), "ffmpeg": _brew("ffmpeg"), "ollama_bin": _brew("ollama"), "ollama_server": _start_ollama,
    "llm": _pull_llm, "whisper": _download_whisper, "embedding": _download_embedding, "voice": _voice,
    "vision": _pip("pyobjc-framework-Vision>=10"), "eventkit": _pip("pyobjc-framework-EventKit>=10"),
}
TITLES = {"packages": "Python-пакеты", "ffmpeg": "ffmpeg", "ollama_bin": "Ollama", "ollama_server": "Запуск Ollama", "llm": "Модель анализа",
          "whisper": "Модель распознавания речи", "embedding": "Модель поиска", "voice": "Голоса говорящих",
          "vision": "Текст на фото", "eventkit": "Календарь и Напоминания"}
