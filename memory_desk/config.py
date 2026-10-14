"""Пути и пользовательские настройки Memory Desk."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import asr

PROJECT_ROOT = Path(os.environ.get("MEMORY_DESK_ROOT") or Path(__file__).resolve().parents[1]).resolve()
CONFIG_PATH = Path(
    os.environ.get("MEMORY_DESK_CONFIG") or Path.home() / "Library/Application Support/MemoryDesk/config.json"
)
PORT = int(os.environ.get("MEMORY_DESK_PORT", "8502"))
OLLAMA_URL = os.environ.get("MEMORY_DESK_OLLAMA_URL", "http://127.0.0.1:11435")
DEFAULT_LLM = os.environ.get("MEMORY_DESK_OLLAMA_MODEL", "qwen3:4b")
OLLAMA_MODEL = DEFAULT_LLM      # прежнее имя: модель по умолчанию; выбранную в настройках даёт llm_model()
_LLM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,120}$")


def _resolve_data_dir() -> Path:
    explicit = os.environ.get("MEMORY_DESK_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    # Данные первой версии лежали в webui/data — продолжаем использовать их, ничего не переносим.
    legacy = PROJECT_ROOT / "webui" / "data"
    return legacy if legacy.exists() else PROJECT_ROOT / "data"


DATA_DIR = _resolve_data_dir()
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOGS_DIR = PROJECT_ROOT / "logs"
MODEL_CACHE = PROJECT_ROOT / "huggingface-cache"
WEB_DIR = Path(__file__).resolve().parent / "web"


UI_LANGS = ("ru", "en", "fr", "de", "es")


def defaults() -> dict:
    recordings = Path.home() / "Downloads/PLAUD"
    return {
        "recordings_dir": str(recordings),
        "obsidian_dir": str(Path.home() / "Documents/Obsidian Vault/Claude/Встречи"),
        "archive_dir": str(recordings / "Обработано"),
        "retention_days": 14,
        "auto_process": False,
        "diarize": True,
        "voice_enabled": False,
        "calendar_source": "",
        "apple_calendar": False,
        "reminders_enabled": False,
        "reminders_list": "Memory Desk",
        "reminders_scope": "mine",
        "my_name": "",
        "notify": True,
        "morning_brief": True,
        "morning_hour": 8,
        "backup_auto": True,
        "backup_interval_hours": 24,
        "backup_keep": 7,
        "backup_on_change": True,
        "note_writeback": False,
        "mirror_enabled": True,
        "default_template": "meeting",
        "lock_timeout_min": 15,
        "suggestions_auto": True,
        "dossier_model": True,
        "ui_lang": "ru",
        "asr_model": "turbo",
        "asr_hint": True,
        "asr_prep": "off",
        "llm_model": "",
        "setup_done": False,
    }


def load_config() -> dict:
    values = defaults()
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                values.update({k: v for k, v in saved.items() if k in values})
        except (OSError, json.JSONDecodeError):
            pass
    return values


def save_config(values: dict) -> dict:
    normalized = defaults()
    normalized.update({k: v for k, v in values.items() if k in normalized})
    try:
        normalized["retention_days"] = max(1, int(normalized["retention_days"]))
    except (TypeError, ValueError):
        raise ValueError("Срок хранения должен быть числом дней")
    normalized["auto_process"] = bool(normalized["auto_process"])
    normalized["diarize"] = bool(normalized["diarize"])
    normalized["voice_enabled"] = bool(normalized["voice_enabled"])
    normalized["calendar_source"] = str(normalized.get("calendar_source") or "").strip()
    normalized["apple_calendar"] = bool(normalized["apple_calendar"])
    normalized["reminders_enabled"] = bool(normalized["reminders_enabled"])
    normalized["reminders_list"] = " ".join(str(normalized.get("reminders_list") or "").split()) or "Memory Desk"
    normalized["reminders_scope"] = "all" if normalized.get("reminders_scope") == "all" else "mine"
    normalized["my_name"] = " ".join(str(normalized.get("my_name") or "").split())
    normalized["notify"] = bool(normalized["notify"])
    normalized["backup_auto"] = bool(normalized["backup_auto"])
    normalized["backup_on_change"] = bool(normalized["backup_on_change"])
    normalized["note_writeback"] = bool(normalized["note_writeback"])
    normalized["mirror_enabled"] = bool(normalized["mirror_enabled"])
    normalized["morning_brief"] = bool(normalized["morning_brief"])
    normalized["suggestions_auto"] = bool(normalized["suggestions_auto"])
    normalized["dossier_model"] = bool(normalized["dossier_model"])
    if normalized.get("ui_lang") not in UI_LANGS:
        normalized["ui_lang"] = "ru"
    normalized["asr_model"] = str(normalized.get("asr_model") or "turbo").strip()
    if not asr.valid_model(normalized["asr_model"]):
        raise ValueError("Модель распознавания: turbo, large-v3, medium или полное имя вида автор/модель")
    normalized["llm_model"] = str(normalized.get("llm_model") or "").strip()
    if normalized["llm_model"] and not _LLM_NAME.match(normalized["llm_model"]):
        raise ValueError("Модель анализа: имя вида qwen3:8b (как в `ollama list`)")
    normalized["asr_hint"] = bool(normalized["asr_hint"])
    normalized["setup_done"] = bool(normalized.get("setup_done"))
    if normalized.get("asr_prep") not in asr.PREP_MODES:
        normalized["asr_prep"] = "off"
    try:
        normalized["morning_hour"] = min(23, max(0, int(normalized["morning_hour"])))
    except (TypeError, ValueError):
        raise ValueError("Час утренней сводки должен быть числом от 0 до 23")
    try:
        normalized["backup_interval_hours"] = min(24 * 30, max(1, int(normalized["backup_interval_hours"])))
        normalized["backup_keep"] = min(60, max(1, int(normalized["backup_keep"])))
    except (TypeError, ValueError):
        raise ValueError("Частота и число резервных копий должны быть числами")
    try:
        normalized["lock_timeout_min"] = int(normalized["lock_timeout_min"])
    except (TypeError, ValueError):
        normalized["lock_timeout_min"] = 15
    if normalized["lock_timeout_min"] not in (1, 5, 15, 30, 60):
        normalized["lock_timeout_min"] = 15
    normalized["default_template"] = str(normalized.get("default_template") or "meeting").strip() or "meeting"
    for key in ("recordings_dir", "obsidian_dir", "archive_dir"):
        text = str(normalized[key]).strip()
        if not text:
            raise ValueError("Путь к папке не может быть пустым")
        normalized[key] = str(Path(text).expanduser().resolve())
    global _LLM_CACHE
    _LLM_CACHE = (-1, "")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


_LLM_CACHE: tuple[int, str] = (-1, "")


def llm_model(cfg: dict | None = None) -> str:
    """Модель анализа (Ollama): выбранная в настройках, иначе по умолчанию. Файл настроек читается заново при его изменении."""
    global _LLM_CACHE
    if cfg is not None:
        chosen = str(cfg.get("llm_model") or "").strip()
        return chosen or DEFAULT_LLM
    try:
        stamp = CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        return DEFAULT_LLM
    if _LLM_CACHE[0] != stamp:
        _LLM_CACHE = (stamp, llm_model(load_config()))
    return _LLM_CACHE[1]
