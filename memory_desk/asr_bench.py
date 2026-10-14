"""Замер качества распознавания: доля ошибочных слов (WER) и символов (CER) против эталона, попадание в термины словаря.

Чистые функции без моделей: их использует `scripts/asr_benchmark.py` и проверяют тесты. Эталон — текст, который вы
сами вычитали по записи (хватит 3–5 минут речи из 3–5 разных записей). Модели в этот модуль не импортируются.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_STAMP = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")
_SPEAKER = re.compile(r"\*\*[^*:\n]{1,40}:\*\*")
_NOT_WORD = re.compile(r"[^\w\s]|_", re.UNICODE)


def normalize(text: str) -> list[str]:
    """Слова для сравнения: без таймкодов, меток говорящих и пунктуации, в нижнем регистре, «ё» = «е»."""
    text = _SPEAKER.sub(" ", _STAMP.sub(" ", text)).casefold().replace("ё", "е")
    return _NOT_WORD.sub(" ", text).split()


def _distance(a: list, b: list) -> int:
    if not a:
        return len(b)
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    return _distance(ref, hyp) / len(ref) if ref else (0.0 if not hyp else 1.0)


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = " ".join(normalize(reference)), " ".join(normalize(hypothesis))
    return _distance(list(ref), list(hyp)) / len(ref) if ref else (0.0 if not hyp else 1.0)


def term_recall(reference: str, hypothesis: str, terms: list[str]) -> tuple[int, int]:
    """(сколько раз термин из словаря встречается в эталоне, сколько из них нашлось в распознанном тексте)."""
    ref, hyp = " ".join(normalize(reference)), " ".join(normalize(hypothesis))
    total = found = 0
    for term in terms:
        key = " ".join(normalize(term))
        if not key:
            continue
        pattern = re.compile(r"(?<!\w)" + re.escape(key) + r"(?!\w)")
        in_ref = len(pattern.findall(ref))
        total += in_ref
        found += min(in_ref, len(pattern.findall(hyp)))
    return total, found


@dataclass
class Run:
    """Один прогон: настройки, суммарные ошибки по всем записям и затраченное время."""
    label: str
    ref_words: int = 0
    word_errors: float = 0.0
    ref_chars: int = 0
    char_errors: float = 0.0
    terms: int = 0
    terms_found: int = 0
    seconds: float = 0.0
    audio_seconds: float = 0.0
    files: dict[str, float] = field(default_factory=dict)

    def add(self, name: str, reference: str, hypothesis: str, took: float, audio: float, terms: list[str]) -> None:
        ref = normalize(reference)
        chars = len(" ".join(ref))
        self.ref_words += len(ref)
        self.word_errors += wer(reference, hypothesis) * len(ref)
        self.ref_chars += chars
        self.char_errors += cer(reference, hypothesis) * chars
        total, found = term_recall(reference, hypothesis, terms)
        self.terms += total
        self.terms_found += found
        self.seconds += took
        self.audio_seconds += audio
        self.files[name] = wer(reference, hypothesis)

    @property
    def wer(self) -> float:
        return self.word_errors / self.ref_words if self.ref_words else 0.0

    @property
    def cer(self) -> float:
        return self.char_errors / self.ref_chars if self.ref_chars else 0.0

    @property
    def speed(self) -> float:
        """Во сколько раз расшифровка быстрее самой записи (0 — время не измерялось)."""
        return self.audio_seconds / self.seconds if self.seconds else 0.0


def report(runs: list[Run]) -> str:
    """Таблица в Markdown: лучшие по WER сверху."""
    lines = ["| Настройки | WER | CER | Термины | Скорость | Записей |", "|---|---:|---:|---:|---:|---:|"]
    for run in sorted(runs, key=lambda r: (r.wer, r.label)):
        terms = f"{run.terms_found}/{run.terms}" if run.terms else "—"
        speed = f"×{run.speed:.1f}" if run.speed else "—"
        lines.append(f"| {run.label} | {run.wer * 100:.1f} % | {run.cer * 100:.1f} % | {terms} | {speed} | {len(run.files)} |")
    return "\n".join(lines)
