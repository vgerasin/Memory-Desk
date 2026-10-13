"""Распознавание текста на фото: системный движок macOS (Vision) и запасной Tesseract. Всё локально, в сеть ничего не уходит.

Результат — черновик: рукописный текст и снимки под углом распознаются заметно хуже печатного, поэтому кабинет
показывает его как «черновик распознавания» и даёт править. Движок выбирается так: Vision (если стоит
pyobjc-framework-Vision и это macOS), иначе Tesseract (если установлен), иначе распознавания нет, а вложение
сохраняется без текста. Для проверок движок подменяется через `BACKEND`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

LANGUAGES = ("ru-RU", "en-US")
TESSERACT_TIMEOUT = 120
MAX_SIDE = 6000


class OcrError(RuntimeError):
    """Распознать не удалось; текст понятен пользователю."""


@dataclass
class Result:
    text: str
    engine: str          # «Vision», «Tesseract» или имя подмены
    note: str = ""       # предупреждение (например, нет русского языка)


BACKEND: Callable[[Path], Result] | None = None   # подмена для тестов


# --------------------------------------------------------------------------- очистка

def clean(text: str) -> str:
    """Склеивает строки в аккуратный текст: без пустых хвостов, одиночных символов-шума и лишних пробелов."""
    lines = []
    for raw in str(text or "").replace("\r", "\n").splitlines():
        line = " ".join(raw.split())
        if not line or (len(line) < 2 and not line.isalnum()):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- Vision (macOS)

def _vision_available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import Vision  # noqa: F401
        return True
    except Exception:
        return False


def _vision_languages(vision) -> list[str]:
    """Русский поддерживается с macOS 13; отсутствие списка поддерживаемых языков — не повод отказаться."""
    request = vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(vision.VNRequestTextRecognitionLevelAccurate)
    try:
        supported, _error = request.supportedRecognitionLanguagesAndReturnError_(None)
        supported = [str(s) for s in (supported or [])]
    except Exception:
        supported = []
    if not supported:
        return list(LANGUAGES)
    chosen = [lang for lang in LANGUAGES if lang in supported]
    return chosen or list(LANGUAGES)


def _order(observations) -> list[str]:
    """Строки сверху вниз, а при равной высоте слева направо (рамка Vision отсчитывается снизу)."""
    rows = []
    for item in observations:
        candidates = item.topCandidates_(1)
        if not candidates:
            continue
        box = item.boundingBox()
        rows.append((box.origin.y + box.size.height / 2, box.origin.x, box.size.height, str(candidates[0].string())))
    rows.sort(key=lambda r: (-r[0], r[1]))
    lines: list[str] = []
    last_y = None
    last_h = 0.02
    for y, _x, height, text in rows:
        if last_y is not None and abs(y - last_y) < max(last_h, height) * 0.5 and lines:
            lines[-1] = f"{lines[-1]} {text}"      # обрывки одной строки
        else:
            lines.append(text)
        last_y, last_h = y, height
    return lines


def vision(path: Path) -> Result:
    import Vision
    from Foundation import NSURL
    url = NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(_vision_languages(Vision))
    request.setUsesLanguageCorrection_(True)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise OcrError(f"Vision не смог разобрать снимок: {error}")
    return Result(clean("\n".join(_order(request.results() or []))), "Vision")


# --------------------------------------------------------------------------- Tesseract

def tesseract_path() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
        if os.path.exists(candidate):
            return candidate
    return None


def _tesseract_languages(binary: str) -> list[str]:
    try:
        out = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines()[1:] if line.strip()]


def _prepare(path: Path, folder: Path) -> Path:
    """Поворот по EXIF, оттенки серого, увеличение мелких снимков: так Tesseract читает заметно лучше."""
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 200_000_000
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("L")
    image = ImageOps.autocontrast(image)
    if image.width < 1500:
        scale = 2 if image.width >= 700 else 3
        image = image.resize((image.width * scale, image.height * scale), Image.LANCZOS)
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    target = folder / "page.png"
    image.save(target)
    return target


def tesseract(path: Path) -> Result:
    binary = tesseract_path()
    if not binary:
        raise OcrError("Tesseract не установлен")
    have = _tesseract_languages(binary)
    languages = [lang for lang in ("rus", "eng") if lang in have] or ["eng"]
    note = "" if "rus" in languages else "В Tesseract нет русского языка: установите tesseract-lang (brew install tesseract-lang)"
    with tempfile.TemporaryDirectory(prefix="md-ocr-") as tmp:
        image = _prepare(path, Path(tmp))
        try:
            done = subprocess.run([binary, str(image), "stdout", "-l", "+".join(languages), "--psm", "6"],
                                  capture_output=True, text=True, timeout=TESSERACT_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise OcrError("Распознавание заняло слишком много времени") from exc
        except OSError as exc:
            raise OcrError(f"Tesseract не запустился: {exc}") from exc
    if done.returncode != 0:
        raise OcrError("Tesseract не смог разобрать снимок: " + (done.stderr or "").strip()[:200])
    return Result(clean(done.stdout), "Tesseract", note)


# --------------------------------------------------------------------------- выбор движка

def engines() -> list[str]:
    """Доступные движки по порядку предпочтения (для «Состояния системы»)."""
    found = []
    if BACKEND:
        found.append("подмена")
    if _vision_available():
        found.append("Vision")
    if tesseract_path():
        found.append("Tesseract")
    return found


def recognize(path: Path) -> Result:
    """Текст со снимка. Если ни одного движка нет, бросает OcrError с подсказкой, что поставить."""
    if BACKEND:
        return BACKEND(path)
    errors = []
    if _vision_available():
        try:
            return vision(path)
        except Exception as exc:    # любой сбой Vision — повод попробовать запасной движок
            errors.append(f"Vision: {exc}")
    if tesseract_path():
        try:
            return tesseract(path)
        except OcrError as exc:
            errors.append(str(exc))
    if errors:
        raise OcrError("; ".join(errors))
    raise OcrError("Нет движка распознавания: на macOS поставится Vision при запуске start.command, "
                   "запасной вариант — brew install tesseract tesseract-lang")


def looks_like_text(value: str) -> bool:
    return bool(re.search(r"\w{2,}", value or ""))
