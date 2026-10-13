"""Пути и пользовательские настройки Memory Desk."""
from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("MEMORY_DESK_ROOT") or Path(__file__).resolve().parents[1]).resolve()
CONFIG_PATH = Path(
    os.environ.get("MEMORY_DESK_CONFIG") or Path.home() / "Library/Application Support/MemoryDesk/config.json"
)
PORT = int(os.environ.get("MEMORY_DESK_PORT", "8502"))
OLLAMA_URL = os.environ.get("MEMORY_DESK_OLLAMA_URL", "http://127.0.0.1:11435")
OLLAMA_MODEL = os.environ.get("MEMORY_DESK_OLLAMA_MODEL", "qwen3:4b")


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
        "default_template": "meeting",
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
    normalized["morning_brief"] = bool(normalized["morning_brief"])
    try:
        normalized["morning_hour"] = min(23, max(0, int(normalized["morning_hour"])))
    except (TypeError, ValueError):
        raise ValueError("Час утренней сводки должен быть числом от 0 до 23")
    try:
        normalized["backup_interval_hours"] = min(24 * 30, max(1, int(normalized["backup_interval_hours"])))
        normalized["backup_keep"] = min(60, max(1, int(normalized["backup_keep"])))
    except (TypeError, ValueError):
        raise ValueError("Частота и число резервных копий должны быть числами")
    normalized["default_template"] = str(normalized.get("default_template") or "meeting").strip() or "meeting"
    for key in ("recordings_dir", "obsidian_dir", "archive_dir"):
        text = str(normalized[key]).strip()
        if not text:
            raise ValueError("Путь к папке не может быть пустым")
        normalized[key] = str(Path(text).expanduser().resolve())
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized
