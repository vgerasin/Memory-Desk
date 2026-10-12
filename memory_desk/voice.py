"""Голоса: локальные голосовые отпечатки, разбиение записи по голосу, сопоставление с людьми.

Модель SpeechBrain ECAPA-TDNN скачивается один раз с публичной страницы Hugging Face (без токена и регистрации)
и дальше работает полностью офлайн. Отпечаток голоса — биометрическая информация: образец добавляется только
по явному действию в карточке человека и хранится в локальной базе; удалить его можно там же.

Всё, что требует torch/speechbrain, подключается лениво: без них приложение работает, а голосовые функции
показывают причину недоступности.
"""
from __future__ import annotations

import json
import importlib
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import numpy as np

from . import config, storage

MODEL_REPO = "speechbrain/spkrec-ecapa-voxceleb"
DEFAULT_FILES = ["hyperparams.yaml", "embedding_model.ckpt", "mean_var_norm_emb.ckpt", "classifier.ckpt", "label_encoder.ckpt"]
SAMPLE_RATE = 16000
WINDOW_SEC = 3.0
HOP_SEC = 1.5
MIN_SEGMENT_SEC = 0.8
MAX_WINDOWS = 4          # окон на одну реплику: хватает для надёжного отпечатка и держит скорость
CLUSTER_SIM = 0.45       # косинусное сходство, выше которого реплики считаются одним голосом
MIN_CLUSTER_SEC = 6.0    # «говорящие» короче этого — шум: присоединяем к ближайшему
MAX_SPEAKERS = 8
MATCH_THRESHOLD = 0.55   # сходство с профилем, при котором голос считается узнанным
MATCH_MARGIN = 0.05      # и должно быть заметно выше, чем у второго кандидата


def model_dir() -> Path:
    return config.MODEL_CACHE / "speechbrain-ecapa"


def model_ready() -> bool:
    folder = model_dir()
    return all((folder / name).exists() for name in ("hyperparams.yaml", "embedding_model.ckpt"))


def _log_import_error(module: str) -> None:
    """Полный текст ошибки импорта — в logs/voice-error.log: по нему видно, какой именно компонент не загрузился."""
    try:
        import traceback  # noqa: PLC0415
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        (config.LOGS_DIR / "voice-error.log").write_text(f"import {module}\n\n{traceback.format_exc()}", encoding="utf-8")
    except OSError:
        pass


def dependencies() -> tuple[bool, str]:
    missing = []
    for module in ("torch", "speechbrain", "scipy"):
        try:
            if module == "speechbrain":
                _ensure_torchaudio()
            __import__(module)
        except Exception as exc:  # noqa: BLE001 — ImportError и ошибки инициализации нативных библиотек
            missing.append(f"{module} ({type(exc).__name__}: {str(exc)[:160]})" if not isinstance(exc, ModuleNotFoundError)
                           else f"{module} ({type(exc).__name__})")
            _log_import_error(module)
    if missing:
        return False, "Не установлены компоненты: " + ", ".join(missing) + ". Нажмите «Подготовить голос» в настройках"
    if not shutil.which("ffmpeg"):
        return False, "Не найден ffmpeg"
    return True, ""


def _ensure_torchaudio() -> None:
    """SpeechBrain импортирует torchaudio на верхнем уровне, хотя для расчёта отпечатка голоса он не нужен
    (звук читает ffmpeg, признаки считает сам torch). У torchaudio нет сборок под свежие версии torch, а ставить его
    значит откатить torch. Поэтому, если настоящего torchaudio нет, подставляется заглушка: она падает только при реальном вызове."""
    import sys as _sys  # noqa: PLC0415
    import types  # noqa: PLC0415
    if "torchaudio" in _sys.modules:
        return
    try:
        import torchaudio  # noqa: F401, PLC0415
        return
    except Exception:  # noqa: BLE001 — не установлен или собран под другой torch
        for name in [n for n in _sys.modules if n == "torchaudio" or n.startswith("torchaudio.")]:
            del _sys.modules[name]

    class Unavailable:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("torchaudio не установлен: эта функция SpeechBrain здесь не используется")

    def make(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        module.__getattr__ = lambda attr: (_ for _ in ()).throw(AttributeError(attr)) if attr.startswith("__") else Unavailable  # type: ignore[attr-defined]
        return module

    names = ["torchaudio", "torchaudio.transforms", "torchaudio.functional", "torchaudio.compliance",
             "torchaudio.compliance.kaldi", "torchaudio.sox_effects", "torchaudio.backend", "torchaudio.io"]
    for name in names:
        _sys.modules[name] = make(name)
    for name in names[1:]:
        parent, _, child = name.rpartition(".")
        setattr(_sys.modules[parent], child, _sys.modules[name])


# --------------------------------------------------------------------------- подготовка: компоненты и модель

_download = {"state": "idle", "phase": "", "done": 0, "total": 0, "message": ""}
_download_lock = threading.Lock()


def download_status() -> dict:
    with _download_lock:
        return dict(_download)


def start_download() -> bool:
    """Ставит недостающие Python-компоненты и скачивает модель. False — если подготовка уже идёт."""
    with _download_lock:
        if _download["state"] == "running":
            return False
        _download.update(state="running", phase="", done=0, total=0, message="")
    threading.Thread(target=_prepare, name="voice-setup", daemon=True).start()
    return True


def _set(**values) -> None:
    with _download_lock:
        _download.update(values)


def pip_command() -> list[str] | None:
    """Как поставить пакет в окружение, где запущен сервер: pip, ensurepip или uv."""
    probe = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, check=False)
    if probe.returncode != 0:
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], capture_output=True, check=False)
        probe = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, check=False)
    if probe.returncode == 0:
        return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    uv = shutil.which("uv")
    return [uv, "pip", "install", "--python", sys.executable] if uv else None


def install_plan(installed_torch: str | None) -> list[list[str]]:
    """Наборы аргументов pip. SpeechBrain ставится без зависимостей: иначе pip потянет torchaudio и заменит torch
    версией, под которую собран torchaudio. Нужные ему лёгкие пакеты ставятся отдельно."""
    plan = []
    if not installed_torch:
        plan.append(["torch"])
    plan.append(["--no-deps", "speechbrain"])
    plan.append(["hyperpyyaml", "joblib", "sentencepiece", "soundfile", "tqdm", "huggingface_hub", "requests", "packaging", "scipy"])
    return plan


def _install_dependencies() -> None:
    ok, _reason = dependencies()
    if ok:
        return
    _set(phase="install", message="Устанавливаю компоненты распознавания голоса")
    base = pip_command()
    if not base:
        raise RuntimeError("В окружении нет pip и uv: запустите install.command")
    try:
        torch_version = importlib.import_module("torch").__version__
    except Exception:  # noqa: BLE001
        torch_version = None
    for args in install_plan(torch_version):
        result = subprocess.run(base + args, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
            raise RuntimeError(f"не удалось установить {' '.join(a for a in args if not a.startswith('--'))}: " + " ".join(tail))
    importlib.invalidate_caches()
    ok, reason = dependencies()
    if not ok:
        raise RuntimeError(reason)


def _prepare() -> None:
    try:
        _install_dependencies()
        if not model_ready():
            _download_model()
        _set(state="done", message="Голос готов к работе")
    except Exception as exc:  # noqa: BLE001
        _set(state="error", message=f"Не удалось подготовить голос: {exc}")


def _download_model() -> None:
    folder = model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    _set(phase="model", done=0, total=0, message="Скачиваю модель голоса")
    base = f"https://huggingface.co/{MODEL_REPO}/resolve/main/"
    names = list(DEFAULT_FILES)
    try:  # актуальный список файлов; если API недоступен — известный набор
        with urllib.request.urlopen(f"https://huggingface.co/api/models/{MODEL_REPO}", timeout=20) as response:
            listed = [s["rfilename"] for s in json.load(response).get("siblings", [])]
        found = [n for n in listed if n.endswith((".ckpt", ".yaml")) and "/" not in n]
        if "embedding_model.ckpt" in found and "hyperparams.yaml" in found:
            names = found
    except (OSError, ValueError, KeyError):
        pass
    sizes = {}
    for name in names:
        request = urllib.request.Request(base + name, method="HEAD")
        with urllib.request.urlopen(request, timeout=30) as response:
            sizes[name] = int(response.headers.get("Content-Length") or 0)
    _set(total=sum(sizes.values()))
    done = 0
    for name in names:
        target = folder / name
        if target.exists() and sizes.get(name) and target.stat().st_size == sizes[name]:
            done += sizes[name]
            _set(done=done)
            continue
        part = target.with_name(name + ".part")
        with urllib.request.urlopen(base + name, timeout=60) as response, part.open("wb") as out:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                _set(done=done)
        part.replace(target)


# --------------------------------------------------------------------------- кодировщик

class EcapaEncoder:
    """ECAPA-TDNN из локальной папки. Считает 192-мерный нормированный отпечаток по фрагменту звука 16 кГц.

    Модель собирается напрямую из hyperparams.yaml и весов, без from_hparams и Pretrainer SpeechBrain: так работа
    не зависит от сети и от того, как именно устроены пути в yaml. Последовательность та же, что в
    EncoderClassifier.encode_batch: признаки → нормировка по записи → ECAPA."""

    def __init__(self) -> None:
        import torch  # noqa: PLC0415
        _ensure_torchaudio()
        from hyperpyyaml import load_hyperpyyaml  # noqa: PLC0415
        from speechbrain.utils.checkpoints import DEFAULT_TRANSFER_HOOKS, get_default_hook  # noqa: PLC0415
        self.torch = torch
        folder = model_dir()
        text = (folder / "hyperparams.yaml").read_text(encoding="utf-8")
        try:
            params = load_hyperpyyaml(text, overrides={"pretrained_path": str(folder)})
        except Exception:  # noqa: BLE001 — в yaml может не быть этой переменной
            params = load_hyperpyyaml(text)
        for name, filename in (("embedding_model", "embedding_model.ckpt"),):  # нормировка эмбеддингов не нужна: сравнение по косинусу
            target = params.get(name)
            path = folder / filename
            if target is None or not path.exists():
                continue
            hook = get_default_hook(target, DEFAULT_TRANSFER_HOOKS)
            if hook is None:
                raise RuntimeError(f"Не удалось загрузить {filename}")
            hook(target, path)
        self.features = params["compute_features"]
        self.normalize = params["mean_var_norm"]
        self.model = params["embedding_model"]
        for module in (self.features, self.normalize, self.model):
            if hasattr(module, "eval"):
                module.eval()

    def embed(self, samples: np.ndarray) -> np.ndarray:
        torch = self.torch
        wav = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32))[None]
        lens = torch.ones(1)
        with torch.no_grad():
            feats = self.features(wav)
            feats = self.normalize(feats, lens)
            vector = self.model(feats, lens).squeeze().cpu().numpy()
        return _unit(vector)


_encoder = None
_encoder_lock = threading.Lock()


def get_encoder():
    """Единый кодировщик на процесс. В тестах подменяется через use_encoder."""
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            ok, reason = dependencies()
            if not ok:
                raise RuntimeError(reason)
            if not model_ready():
                raise RuntimeError("Модель голоса ещё не загружена: скачайте её в настройках")
            _encoder = EcapaEncoder()
        return _encoder


def use_encoder(encoder) -> None:
    global _encoder
    with _encoder_lock:
        _encoder = encoder


def available() -> tuple[bool, str]:
    """Можно ли прямо сейчас определять говорящих по голосу."""
    if _encoder is not None:
        return True, ""
    ok, reason = dependencies()
    if not ok:
        return False, reason
    if not model_ready():
        return False, "Модель голоса ещё не загружена"
    return True, ""


# --------------------------------------------------------------------------- звук

def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-9 else vector


def load_audio(path: str | Path) -> np.ndarray:
    """Вся запись как int16 моно 16 кГц (экономит память: час звука — около 115 МБ)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Не найден ffmpeg")
    result = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
                            capture_output=True, check=False)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("Не удалось прочитать звук записи")
    return np.frombuffer(result.stdout, dtype=np.int16)


def segment_spans(segments: list[dict], total_seconds: float) -> list[tuple[float, float]]:
    """Начало и конец каждой реплики: конец — начало следующей (но не длиннее 30 с)."""
    spans = []
    for i, seg in enumerate(segments):
        start = float(seg.get("sec", 0))
        nxt = float(segments[i + 1]["sec"]) if i + 1 < len(segments) else start + 10.0
        end = min(nxt, start + 30.0) if nxt > start else start + 3.0
        spans.append((start, min(end, total_seconds) if total_seconds else end))
    return spans


def segment_embeddings(audio: np.ndarray, spans: list[tuple[float, float]], encoder, progress=None) -> list[np.ndarray | None]:
    result: list[np.ndarray | None] = []
    for index, (start, end) in enumerate(spans):
        chunk = audio[int(start * SAMPLE_RATE): int(end * SAMPLE_RATE)]
        seconds = len(chunk) / SAMPLE_RATE
        if seconds < MIN_SEGMENT_SEC:
            result.append(None)
        else:
            wave = chunk.astype(np.float32) / 32768.0
            if float(np.sqrt(np.mean(wave ** 2))) < 0.002:  # тишина
                result.append(None)
            else:
                size, hop = int(WINDOW_SEC * SAMPLE_RATE), int(HOP_SEC * SAMPLE_RATE)
                if len(wave) <= size * 1.5:
                    result.append(encoder.embed(wave))
                else:
                    starts = list(range(0, len(wave) - size + 1, hop))
                    if len(starts) > MAX_WINDOWS:
                        starts = [starts[round(i * (len(starts) - 1) / (MAX_WINDOWS - 1))] for i in range(MAX_WINDOWS)]
                    result.append(_unit(np.mean([encoder.embed(wave[s:s + size]) for s in starts], axis=0)))
        if progress and (index % 20 == 0 or index == len(spans) - 1):
            progress(index + 1, len(spans))
    return result


# --------------------------------------------------------------------------- кластеризация

def cluster(embeddings: list[np.ndarray | None], seconds: list[float], sim: float = CLUSTER_SIM,
            max_speakers: int = MAX_SPEAKERS) -> list[int]:
    """Номер голоса для каждой реплики (0, 1, …) в порядке первого появления."""
    from scipy.cluster.hierarchy import fcluster, linkage  # noqa: PLC0415

    known = [i for i, e in enumerate(embeddings) if e is not None]
    if not known:
        return [0] * len(embeddings)
    matrix = np.vstack([embeddings[i] for i in known])
    groups = np.zeros(len(known), dtype=int)
    if len(known) > 1:
        groups = fcluster(linkage(matrix, method="average", metric="cosine"), t=1 - sim, criterion="distance") - 1

    def centroids() -> dict[int, np.ndarray]:
        out = {}
        for g in set(groups.tolist()):
            mask = groups == g
            weights = np.array([max(seconds[known[k]], 0.5) for k in np.where(mask)[0]])
            out[g] = _unit((matrix[mask] * weights[:, None]).sum(axis=0))
        return out

    def total_seconds(g: int) -> float:
        return float(sum(seconds[known[k]] for k in np.where(groups == g)[0]))

    while len(set(groups.tolist())) > 1:
        cents = centroids()
        small = [g for g in cents if total_seconds(g) < MIN_CLUSTER_SEC]
        crowded = len(cents) > max_speakers
        if not small and not crowded:
            break
        victim = min(small, key=total_seconds) if small else min(cents, key=total_seconds)
        others = [g for g in cents if g != victim]
        nearest = max(others, key=lambda g: float(cents[victim] @ cents[g]))
        groups[groups == victim] = nearest

    labels: list[int | None] = [None] * len(embeddings)
    for position, i in enumerate(known):
        labels[i] = int(groups[position])
    last = next((labels[i] for i in known), 0)
    for i in range(len(labels)):  # реплики без отпечатка (слишком короткие) наследуют соседнего говорящего
        if labels[i] is None:
            labels[i] = last
        else:
            last = labels[i]
    order: dict[int, int] = {}
    return [order.setdefault(int(g), len(order)) for g in labels]  # type: ignore[arg-type]


def centroids_by_group(embeddings: list[np.ndarray | None], seconds: list[float], groups: list[int]) -> dict[int, tuple[np.ndarray, float]]:
    out: dict[int, tuple[np.ndarray, float]] = {}
    for g in set(groups):
        vectors, weights = [], []
        for i, group in enumerate(groups):
            if group == g and embeddings[i] is not None:
                vectors.append(embeddings[i])
                weights.append(max(seconds[i], 0.5))
        if vectors:
            w = np.array(weights)
            out[g] = (_unit((np.vstack(vectors) * w[:, None]).sum(axis=0)), float(w.sum()))
    return out


# --------------------------------------------------------------------------- профили людей

def to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


def load_profiles() -> list[dict]:
    storage.initialize()
    rows = storage.rows("SELECT v.person_id, v.embedding, v.samples, v.seconds, p.name FROM voice_profiles v "
                        "JOIN people p ON p.id=v.person_id")
    return [{"person_id": r["person_id"], "name": r["name"], "embedding": from_blob(r["embedding"]),
             "samples": r["samples"], "seconds": r["seconds"]} for r in rows]


def match_profiles(centroids: dict[str, np.ndarray], profiles: list[dict]) -> dict[str, dict]:
    """label → {name, score}. Один человек не может быть назначен двум голосам."""
    if not profiles:
        return {}
    scores = {label: sorted(((float(vec @ p["embedding"]), p["name"]) for p in profiles), reverse=True)
              for label, vec in centroids.items()}
    candidates = []
    for label, ranked in scores.items():
        best = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else -1.0
        if best[0] >= MATCH_THRESHOLD and best[0] - second >= MATCH_MARGIN:
            candidates.append((best[0], label, best[1]))
    result: dict[str, dict] = {}
    used: set[str] = set()
    for score, label, name in sorted(candidates, reverse=True):
        if name not in used:
            used.add(name)
            result[label] = {"name": name, "score": round(score, 3)}
    return result


def diarize_by_voice(audio_path: str | Path, segments: list[dict], *, encoder=None, profiles: list[dict] | None = None,
                     progress=None) -> dict:
    """Метки говорящих по голосу. Узнанные люди получают имя, остальные — «Спикер N».
    Возвращает {"labels": [...], "speakers": {label: {embedding, seconds, matched, score}}};
    labels пуст, если в записи один неузнанный голос."""
    encoder = encoder or get_encoder()
    audio = load_audio(audio_path)
    total = len(audio) / SAMPLE_RATE
    spans = segment_spans(segments, total)
    embeddings = segment_embeddings(audio, spans, encoder, progress)
    seconds = [max(0.0, e - s) for s, e in spans]
    groups = cluster(embeddings, seconds)
    cents = centroids_by_group(embeddings, seconds, groups)
    names = match_profiles({str(g): vec for g, (vec, _w) in cents.items()}, profiles or [])
    label_of: dict[int, str] = {}
    generic = 0
    for g in sorted(cents):
        if str(g) in names:
            label_of[g] = names[str(g)]["name"]
        else:
            generic += 1
            label_of[g] = f"Спикер {generic}"
    for g in set(groups):
        label_of.setdefault(g, f"Спикер {len(label_of) + 1}")
    speakers = {label_of[g]: {"embedding": vec.tolist(), "seconds": round(w, 1), "matched": str(g) in names,
                              "score": names[str(g)]["score"] if str(g) in names else None}
                for g, (vec, w) in cents.items()}
    if len(set(groups)) < 2 and not any(s["matched"] for s in speakers.values()):
        return {"labels": [], "speakers": {}}
    return {"labels": [label_of[g] for g in groups], "speakers": speakers}


# --------------------------------------------------------------------------- хранение

def save_meeting_voices(meeting_id: str, speakers: dict) -> None:
    storage.initialize()
    with storage.connect() as db:
        db.execute("DELETE FROM meeting_voices WHERE meeting_id=?", (meeting_id,))
        for label, info in speakers.items():
            db.execute("INSERT INTO meeting_voices (meeting_id,label,embedding,seconds,matched,score) VALUES (?,?,?,?,?,?)",
                       (meeting_id, label, to_blob(np.array(info["embedding"], dtype=np.float32)), float(info.get("seconds", 0)),
                        1 if info.get("matched") else 0, info.get("score")))


def meeting_voices(meeting_id: str) -> dict[str, dict]:
    return {r["label"]: {"seconds": r["seconds"], "matched": bool(r["matched"]), "score": r["score"]}
            for r in storage.rows("SELECT label,seconds,matched,score FROM meeting_voices WHERE meeting_id=?", (meeting_id,))}


def rename_label(meeting_id: str, old: str, new: str) -> None:
    """Метку переименовали (или объединили с другой): отпечаток переезжает вместе с ней; пустое имя — отпечаток забывается."""
    storage.initialize()
    with storage.connect() as db:
        row = db.execute("SELECT embedding,seconds FROM meeting_voices WHERE meeting_id=? AND label=?", (meeting_id, old)).fetchone()
        if not row or old == new:
            return
        db.execute("DELETE FROM meeting_voices WHERE meeting_id=? AND label=?", (meeting_id, old))
        if not new:
            return
        target = db.execute("SELECT embedding,seconds FROM meeting_voices WHERE meeting_id=? AND label=?", (meeting_id, new)).fetchone()
        if target:
            a, wa = from_blob(row["embedding"]), float(row["seconds"] or 1)
            b, wb = from_blob(target["embedding"]), float(target["seconds"] or 1)
            merged = _unit(a * wa + b * wb)
            db.execute("UPDATE meeting_voices SET embedding=?, seconds=?, matched=0 WHERE meeting_id=? AND label=?",
                       (to_blob(merged), wa + wb, meeting_id, new))
        else:
            db.execute("INSERT INTO meeting_voices (meeting_id,label,embedding,seconds,matched,score) VALUES (?,?,?,?,0,NULL)",
                       (meeting_id, new, row["embedding"], row["seconds"]))


def add_sample(person_id: str, meeting_id: str, label: str) -> dict:
    """Добавляет голос говорящего из записи в профиль человека (взвешенное среднее по длительности речи)."""
    storage.initialize()
    with storage.connect() as db:
        voice = db.execute("SELECT embedding,seconds FROM meeting_voices WHERE meeting_id=? AND label=?", (meeting_id, label)).fetchone()
        if not voice:
            raise ValueError("Для этого говорящего нет голосового отпечатка: определите говорящих «по голосу»")
        if not db.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone():
            raise KeyError("Человек не найден")
        new, seconds = from_blob(voice["embedding"]), float(voice["seconds"] or 1)
        old = db.execute("SELECT embedding,samples,seconds FROM voice_profiles WHERE person_id=?", (person_id,)).fetchone()
        if old:
            total = float(old["seconds"]) + seconds
            merged = _unit(from_blob(old["embedding"]) * float(old["seconds"]) + new * seconds)
            db.execute("UPDATE voice_profiles SET embedding=?, samples=?, seconds=?, updated_at=? WHERE person_id=?",
                       (to_blob(merged), old["samples"] + 1, total, storage.now_iso(), person_id))
        else:
            db.execute("INSERT INTO voice_profiles (person_id,embedding,samples,seconds,updated_at) VALUES (?,?,?,?,?)",
                       (person_id, to_blob(new), 1, seconds, storage.now_iso()))
    return profile_stats(person_id) or {}


def remove_profile(person_id: str) -> bool:
    storage.initialize()
    with storage.connect() as db:
        return db.execute("DELETE FROM voice_profiles WHERE person_id=?", (person_id,)).rowcount > 0


def profile_stats(person_id: str) -> dict | None:
    row = storage.one("SELECT samples,seconds FROM voice_profiles WHERE person_id=?", (person_id,))
    return {"samples": row["samples"], "seconds": round(row["seconds"])} if row else None


def all_profile_stats() -> dict[str, dict]:
    return {r["person_id"]: {"samples": r["samples"], "seconds": round(r["seconds"])}
            for r in storage.rows("SELECT person_id,samples,seconds FROM voice_profiles")}


def ingest_pending(by_path: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Подхватывает отпечатки из work/<запись>/voices.json, которые оставила обработка. by_path: путь заметки → id встречи.
    Возвращает [(id встречи, имена узнанных людей)]."""
    root = config.PROJECT_ROOT / "work"
    done: list[tuple[str, list[str]]] = []
    if not root.exists():
        return done
    for folder in root.iterdir():
        voices, marker, state = folder / "voices.json", folder / "voices.ingested", folder / "state.json"
        if not voices.exists() or marker.exists() or not state.exists():
            continue
        try:
            note = str(json.loads(state.read_text(encoding="utf-8")).get("note", ""))
            meeting_id = by_path.get(note) or by_path.get(str(Path(note).resolve()))
            if not meeting_id:
                continue
            speakers = json.loads(voices.read_text(encoding="utf-8")).get("speakers", {})
            save_meeting_voices(meeting_id, speakers)
            marker.write_text("1", encoding="utf-8")
            done.append((meeting_id, [label for label, info in speakers.items() if info.get("matched")]))
        except (OSError, ValueError):
            continue
    return done
