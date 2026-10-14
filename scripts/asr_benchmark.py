#!/usr/bin/env python3
"""Замер качества распознавания речи на ваших записях.

Папка с записями: рядом с каждым аудиофайлом лежит вычитанный вами эталон с тем же именем и расширением .txt
(`планерка.m4a` + `планерка.txt`). Хватит 3–5 записей по 3–5 минут речи; эталон можно набрать только для куска записи,
тогда укажите `--seconds 300`: берутся первые пять минут.

  python3 scripts/asr_benchmark.py ~/эталон --models turbo large-v3 --prep off light --hint no yes
  python3 scripts/asr_benchmark.py ~/эталон --hypotheses ~/эталон/готовое   # оценить уже готовые стенограммы (модели не запускаются)

Результат: таблица Markdown (WER и CER — доля ошибочных слов и символов, «Термины» — сколько терминов словаря найдено,
«Скорость» — во сколько раз быстрее записи) и файл `asr-benchmark.json`. Скрипт ничего не скачивает сам: модели
подгружаются библиотекой mlx_whisper при первом запуске, если их ещё нет на диске.
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memory_desk import asr, asr_bench, dictionary  # noqa: E402

AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4"}


def pairs(folder: Path) -> list[tuple[Path | None, Path]]:
    """(аудио, эталон) для каждого .txt с аудио рядом; в режиме --hypotheses аудио не нужно."""
    out = []
    for ref in sorted(folder.glob("*.txt")):
        audio = next((ref.with_suffix(ext) for ext in sorted(AUDIO) if ref.with_suffix(ext).exists()), None)
        out.append((audio, ref))
    return out


def prepare(audio: Path, target: Path, prep: str, seconds: int) -> None:
    command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-vn"]
    if seconds:
        command += ["-t", str(seconds)]
    chain = asr.audio_filter(prep)
    if chain:
        command += ["-af", chain]
    subprocess.run([*command, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)], check=True)


def transcribe(wav: Path, model: str, prompt: str) -> str:
    import mlx_whisper  # noqa: PLC0415 — только на Mac с Apple Silicon

    extra = {"initial_prompt": prompt} if prompt else {}
    result = mlx_whisper.transcribe(str(wav), path_or_hf_repo=asr.resolve_model(model), language="ru", word_timestamps=False,
                                    verbose=False, condition_on_previous_text=False, compression_ratio_threshold=2.4,
                                    logprob_threshold=-1.0, no_speech_threshold=0.6, **extra)
    from memory_desk.whisper_filter import clean_result  # noqa: PLC0415
    cleaned, _ = clean_result(result)
    return cleaned["text"]


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--models", nargs="+", default=["turbo"], help="turbo, large-v3, medium или автор/модель")
    parser.add_argument("--prep", nargs="+", default=["off"], choices=asr.PREP_MODES, help="подготовка звука")
    parser.add_argument("--hint", nargs="+", default=["no"], choices=["no", "yes"], help="подсказка из словаря терминов")
    parser.add_argument("--seconds", type=int, default=0, help="брать только первые N секунд записи (0 — целиком)")
    parser.add_argument("--hypotheses", type=Path, help="папка с готовыми стенограммами (имя.txt или имя.md) вместо запуска моделей")
    parser.add_argument("--out", type=Path, default=Path("asr-benchmark.json"))
    args = parser.parse_args()

    terms = [e.term for e in dictionary.load()]
    runs: list[asr_bench.Run] = []
    items = pairs(args.folder)
    if not items:
        print("В папке нет файлов .txt с эталоном.", file=sys.stderr)
        return 1

    if args.hypotheses:
        run = asr_bench.Run("готовые стенограммы")
        for _audio, ref in items:
            hyp = next((p for p in (args.hypotheses / (ref.stem + ".txt"), args.hypotheses / (ref.stem + ".md")) if p.exists()), None)
            if hyp:
                run.add(ref.stem, ref.read_text(encoding="utf-8"), hyp.read_text(encoding="utf-8"), 0.0, 0.0, terms)
        runs.append(run)
    else:
        for model, prep, hint in itertools.product(args.models, args.prep, args.hint):
            run = asr_bench.Run(f"{model} · звук: {prep} · подсказка: {hint}")
            prompt = asr.build_prompt(terms) if hint == "yes" else ""
            for audio, ref in items:
                if audio is None:
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    wav = Path(tmp) / "audio.wav"
                    prepare(audio, wav, prep, args.seconds)
                    length = duration(wav)
                    started = time.monotonic()
                    text = transcribe(wav, model, prompt)
                    took = time.monotonic() - started
                run.add(ref.stem, ref.read_text(encoding="utf-8"), text, took, length, terms)
                print(f"{run.label}: {ref.stem} — WER {run.files[ref.stem] * 100:.1f} %", flush=True)
            runs.append(run)

    print("\n" + asr_bench.report(runs))
    args.out.write_text(json.dumps([{"label": r.label, "wer": r.wer, "cer": r.cer, "terms": r.terms, "terms_found": r.terms_found,
                                     "speed": r.speed, "files": r.files} for r in runs], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nПодробности: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
