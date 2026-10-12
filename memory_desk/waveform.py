"""Огибающая звука для плеера: 64 столбика по RMS через ffmpeg (кэшируется на диск)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config

BARS = 64


def compute(path: Path, bars: int = BARS) -> list[float] | None:
    try:
        import numpy as np
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "2000", "-f", "s16le", "-"],
            capture_output=True, timeout=180, check=True)
        data = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
    except Exception:  # noqa: BLE001 — нет ffmpeg/numpy или файл не читается: плеер нарисует запасную волну
        return None
    if data.size < bars:
        return None
    rms = np.array([float(np.sqrt(np.mean(chunk ** 2))) for chunk in np.array_split(data, bars)])
    peak = float(rms.max()) or 1.0
    return [round(max(0.12, min(1.0, (v / peak) ** 0.7)), 3) for v in rms]


def cached(meeting_id: str, audio: Path) -> list[float] | None:
    folder = config.DATA_DIR / "waveforms"
    target = folder / f"{meeting_id}.json"
    stamp = audio.stat().st_mtime_ns
    if target.exists():
        try:
            payload = json.loads(target.read_text())
            if payload.get("stamp") == stamp:
                return payload["bars"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    bars = compute(audio)
    if bars:
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"stamp": stamp, "bars": bars}))
    return bars
