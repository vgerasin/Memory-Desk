#!/usr/bin/env python3
"""Local PLAUD -> MLX Whisper -> Ollama -> Obsidian pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from memory_desk import asr, dictionary, templates  # noqa: E402
from memory_desk.whisper_filter import clean_result  # noqa: E402
from memory_desk.diarize import detect_speakers, parse as parse_transcript, relabel  # noqa: E402
from memory_desk.llm import ollama_json  # noqa: E402

DEFAULT_VAULT = Path.home() / "Documents/Obsidian Vault/Встречи"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
WHISPER_MODEL = asr.resolve_model(os.environ.get("PLAUD_WHISPER_MODEL", asr.DEFAULT_MODEL))
OLLAMA_MODEL = os.environ.get("PLAUD_OLLAMA_MODEL", "").strip() or "qwen3:4b"   # из настроек кабинета; без них — модель по умолчанию
OLLAMA_URL = os.environ.get("PLAUD_OLLAMA_URL", "http://127.0.0.1:11435")
ANALYSIS_VERSION = 4
PROCESSING_LOG = ROOT / "logs" / "processing.jsonl"


slug = asr.slug  # одна реализация на пайплайн и сервер: по ней сервер находит рабочую папку задания


def run(command: list[str]) -> str:
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


CURRENT_JOB_ID = ""


def log_event(
    event: str, source: Path, detail: str = "", note: Path | None = None,
    *, stage: str = "", percent: int | None = None,
) -> None:
    PROCESSING_LOG.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"), "event": event,
        "source": source.name, "detail": detail, "note": str(note or ""),
        "job_id": CURRENT_JOB_ID, "stage": stage or event,
    }
    if percent is not None:
        payload["percent"] = max(0, min(100, int(percent)))
    with PROCESSING_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def completed_duplicate(source: Path) -> Path | None:
    source_hash = file_hash(source)
    for state_path in (ROOT / "work").glob("*/state.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("status") == "completed" and state.get("source_hash") == source_hash:
            note = Path(str(state.get("note", "")))
            if note.exists():
                return note
    return None


def duration_seconds(audio: Path) -> float:
    value = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio),
    ])
    return float(value)


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def recorded_datetime(source: Path) -> datetime:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})[_ -](\d{2})[_-](\d{2})[_-](\d{2})", source.stem)
    if match:
        try:
            return datetime(*map(int, match.groups()))
        except ValueError:
            pass
    return datetime.fromtimestamp(source.stat().st_mtime)


def normalize_audio(source: Path, target: Path, prep: str = "off") -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    chain = asr.audio_filter(prep)
    command = ["ffmpeg", "-y", "-i", str(source), "-vn"]
    if chain:
        command += ["-af", chain]
    run([*command, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)])


def asr_prompt(terms: list) -> str:
    """Подсказка Whisper: слова словаря, затем имена людей и названия проектов (если это не отключено в настройках)."""
    words = [entry.term for entry in terms]
    if os.environ.get("PLAUD_ASR_HINT", "1") == "0":
        return dictionary.hint(terms)
    context = asr.read_context(asr.context_path_from_env())
    return asr.build_prompt(words, context["people"], context["projects"])


def transcribe(audio: Path, terms: list | None = None) -> dict:
    import mlx_whisper

    extra = {}
    prompt = asr_prompt(terms or [])
    if prompt:
        extra["initial_prompt"] = prompt  # Whisper учитывает подсказку в начале записи; остальное исправит замена по словарю
    return mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=WHISPER_MODEL,
        language="ru",
        word_timestamps=False,
        verbose=False,
        **extra,
        # Без опоры на предыдущий текст модель не зацикливается на музыке и шуме; пороги — защита от выдумок на тишине.
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4,
        logprob_threshold=-1.0,
        no_speech_threshold=0.6,
    )


def transcript_markdown(result: dict) -> str:
    lines = []
    for segment in result.get("segments", []):
        text = str(segment.get("text", "")).strip()
        if text:
            lines.append(f"[{format_time(float(segment.get('start', 0)))}] {text}")
    return "\n\n".join(lines).strip()


OLLAMA_NUM_CTX = 8192
OLLAMA_NUM_PREDICT = 3072
CHUNK_CHARS = 6000


def ollama_chat(prompt: str) -> dict:
    return ollama_json(
        prompt, model=OLLAMA_MODEL, url=OLLAMA_URL, num_ctx=OLLAMA_NUM_CTX, num_predict=OLLAMA_NUM_PREDICT,
        bad_response_path=ROOT / "logs" / "ollama-last-bad-response.txt",
    )


def analysis_prompt(transcript: str, partial: bool = False, template: "templates.Template | None" = None) -> str:
    scope = "фрагмента стенограммы" if partial else "полной стенограммы встречи"
    schema, notes = templates.prompt_lines(template or templates.standard())
    notes_block = f"\nОсобенности этой записи:\n{notes}\n" if notes else ""
    return f"""
Ты — аккуратный секретарь. Проанализируй текст {scope} на русском языке.
Не придумывай факты, имена, решения, сроки или ответственных. Если сведений нет,
используй пустой массив.

Критические правила:
- Метки вида [00:01:16] — это позиции в аудио, а не сроки или даты.
- Метки «Спикер 1», «Спикер 2» — условные обозначения говорящих, а не имена. Не используй их как ответственных.
- Не превращай обсуждение, гипотезу, шутку или отвергнутое предложение в решение.
- Решением считается только явно принятое действие или однозначное согласие участников.
- Задачей считается только явное поручение или обязательство что-то сделать после обсуждения.
- Ответственного указывай только когда он прямо назван рядом с поручением.
- Срок указывай только если в речи явно названа дата или срок выполнения; иначе «не указан».
- Следующие шаги включай только если участники явно договорились выполнить их; не предлагай свои действия.
- Для каждого предложения укажи статус: accepted, rejected или discussed. Не считай предложение принятым без явного согласия.
- Срок задачи записывай как YYYY-MM-DD только при явно названной календарной дате; иначе «не указан».
- Сохраняй смысл исходного текста, даже если это художественный диалог, лекция или запись не является деловой встречей.
{notes_block}
Верни только JSON со структурой:
{{
{schema}
}}

Для каждого факта evidence_time должен совпадать с ближайшей меткой времени в исходном тексте.
Если доказательной метки нет, не включай факт в соответствующий массив.

Текст:
{transcript}
""".strip()


def split_transcript(transcript: str, limit: int = CHUNK_CHARS) -> list[str]:
    chunks, current = [], ""
    for block in transcript.split("\n\n"):
        while len(block) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(block[:limit])
            block = block[limit:]
        if current and len(current) + len(block) + 2 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current.strip():
        chunks.append(current)
    return chunks


def merge_partials(partials: list[dict], extra_keys: tuple[str, ...] = ()) -> dict:
    merged: dict = {"title": "", "summary": [], "topics": []}
    fact_keys = ("proposals", "decisions", "tasks", "risks", "open_questions", "next_steps") + tuple(extra_keys)
    for key in fact_keys:
        merged[key] = []
    seen: set = set()
    for part in partials:
        if not merged["title"]:
            merged["title"] = str(part.get("title") or "").strip()
        for key in ("summary", "topics") + fact_keys:
            values = part.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                marker = (key, json.dumps(value, ensure_ascii=False, sort_keys=True).casefold())
                if marker in seen:
                    continue
                seen.add(marker)
                merged[key].append(value)
    return merged


def summarize_parts(partials: list[dict]) -> dict:
    bullets_in = []
    for index, part in enumerate(partials, 1):
        bullets_in.append(f"Фрагмент {index}: " + "; ".join(str(v) for v in (part.get("summary") or []) if v))
    prompt = (
        "Ниже краткие тезисы последовательных фрагментов одной записи на русском языке. "
        "Не добавляй фактов, которых там нет. Верни только JSON вида "
        '{"title": "короткое предметное название", "summary": ["3-7 основных тезисов"], "topics": ["основные темы"]}.\n\n'
        + "\n".join(bullets_in)
    )
    return ollama_chat(prompt[:12000])


def analyze(transcript: str, template: "templates.Template | None" = None) -> dict:
    chunks = split_transcript(transcript)
    if len(chunks) <= 1:
        return ollama_chat(analysis_prompt(transcript, template=template))

    partials = [ollama_chat(analysis_prompt(chunk, partial=True, template=template)) for chunk in chunks]
    merged = merge_partials(partials, tuple(s.data_key for s in template.free_sections) if template else ())
    try:
        head = summarize_parts(partials)
        if isinstance(head.get("summary"), list) and head["summary"]:
            merged["summary"] = head["summary"]
        if isinstance(head.get("topics"), list) and head["topics"]:
            merged["topics"] = head["topics"]
        if str(head.get("title") or "").strip():
            merged["title"] = str(head["title"]).strip()
    except RuntimeError as exc:
        if "не отвечает" in str(exc):
            raise
    merged["summary"] = merged["summary"][:7]
    return merged


def analyze_with_template(transcript: str, template: "templates.Template") -> tuple[dict, "templates.Template", str]:
    """Анализ по шаблону: (данные, фактически применённый шаблон, причина отката или пустая строка).

    Малая модель хуже держит нестандартную структуру. Если ответ по шаблону пуст или не разобран, запись
    разбирается стандартным шаблоном: заметка в любом случае получает разделы, которые читает кабинет."""
    default = templates.standard()
    if template.sections == default.sections and not template.focus:
        return normalize_analysis(analyze(transcript, template), transcript, template), template, ""
    reason = ""
    try:
        data = normalize_analysis(analyze(transcript, template), transcript, template)
        reason = "; ".join(templates.problems_in(data, template))
        if not reason:
            return data, template, ""
    except RuntimeError as exc:
        if "не отвечает" in str(exc):
            raise
        reason = str(exc)
    return normalize_analysis(analyze(transcript, default), transcript, default), default, reason


def evidence_time(value: object, transcript: str) -> str:
    match = re.fullmatch(r"\d{2}:\d{2}:\d{2}", str(value or "").strip())
    if match and f"[{match.group(0)}]" in transcript:
        return match.group(0)
    return ""


def normalize_facts(values: object, transcript: str, text_key: str = "text") -> list[dict]:
    if not isinstance(values, list):
        return []
    normalized = []
    for value in values:
        if isinstance(value, str):
            text, stamp = value.strip(), ""
        elif isinstance(value, dict):
            text = str(value.get(text_key) or value.get("text") or "").strip()
            stamp = evidence_time(value.get("evidence_time"), transcript)
        else:
            continue
        if text:
            item = dict(value) if isinstance(value, dict) else {}
            item[text_key] = text
            item["evidence_time"] = stamp
            normalized.append(item)
    return normalized


def normalize_analysis(data: dict, transcript: str, template: "templates.Template | None" = None) -> dict:
    for key in ("summary", "topics"):
        if not isinstance(data.get(key), list):
            data[key] = []
    if not isinstance(data.get("title"), str):
        data["title"] = ""
    data["topics"] = [str(v).strip() for v in data.get("topics", []) if str(v).strip()]
    for key in ("proposals", "decisions", "risks", "open_questions", "next_steps"):
        data[key] = normalize_facts(data.get(key), transcript)
    for proposal in data["proposals"]:
        status = str(proposal.get("status") or "discussed").strip().lower()
        proposal["status"] = status if status in {"accepted", "rejected", "discussed"} else "discussed"
    data["tasks"] = normalize_facts(data.get("tasks"), transcript, text_key="task")
    for task in data["tasks"]:
        deadline = str(task.get("deadline") or "").strip()
        if not deadline or re.fullmatch(r"\d{2}:\d{2}:\d{2}", deadline):
            task["deadline"] = "не указан"
        task["owner"] = str(task.get("owner") or "не указано").strip()
        owner = task["owner"]
        stamp = task.get("evidence_time", "")
        if owner.casefold() not in {"не указан", "не указано", ""}:
            blocks = [b for b in transcript.split("\n\n") if b.strip()]
            position = next((i for i, b in enumerate(blocks) if b.startswith(f"[{stamp}]")), -1)
            context = " ".join(blocks[max(0, position - 4):position + 5]).casefold() if position >= 0 else ""
            if owner.casefold() not in context:
                task["owner"] = "не указано"
    if template is not None:
        templates.normalize_free(data, template, lambda value: evidence_time(value, transcript))
        data["_template"] = template.id
    data["_analysis_version"] = ANALYSIS_VERSION
    return data


def bullets(values: object, empty: str = "Не выявлено.") -> str:
    if not isinstance(values, list) or not values:
        return empty
    return "\n".join(f"- {str(value).strip()}" for value in values if str(value).strip()) or empty


def fact_bullets(values: object, empty: str = "Не выявлено.") -> str:
    if not isinstance(values, list) or not values:
        return empty
    lines = []
    for item in values:
        if not isinstance(item, dict) or not item.get("text"):
            continue
        source = f" — `⏱ {item['evidence_time']}`" if item.get("evidence_time") else ""
        lines.append(f"- {item['text']}{source}")
    return "\n".join(lines) or empty


def tasks_markdown(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "Задачи не зафиксированы."
    lines = []
    for item in values:
        if not isinstance(item, dict) or not item.get("task"):
            continue
        owner = item.get("owner") or "не указано"
        deadline = item.get("deadline") or "не указан"
        source = f"; `⏱ {item['evidence_time']}`" if item.get("evidence_time") else ""
        lines.append(f"- [ ] {item['task']} — **ответственный:** {owner}; **срок:** {deadline}{source}")
    return "\n".join(lines) or "Задачи не зафиксированы."


def mermaid_text(value: object) -> str:
    text = re.sub(r"[\[\](){}\"']", "", str(value)).replace("\n", " ").strip()
    return text[:100] or "Не указано"


def mindmap(data: dict) -> str:
    groups = [
        ("Темы", data.get("topics", []), None),
        ("Решения", data.get("decisions", []), "text"),
        ("Задачи", data.get("tasks", []), "task"),
        ("Риски", data.get("risks", []), "text"),
        ("Вопросы", data.get("open_questions", []), "text"),
    ]
    lines = ["```mermaid", "mindmap", f"  root(({mermaid_text(data.get('title', 'Встреча'))}))"]
    node_number = 0
    for group, values, key in groups:
        if not isinstance(values, list) or not values:
            continue
        node_number += 1
        lines.append(f"    group{node_number}[{group}]")
        for value in values[:8]:
            label = value.get(key, "") if key and isinstance(value, dict) else value
            lines.append(f"      item{node_number}_{len(lines)}[{mermaid_text(label)}]")
    lines.append("```")
    return "\n".join(lines)


def decision_flow(data: dict) -> str:
    proposals = data.get("proposals", [])
    decisions = data.get("decisions", [])
    if not proposals and not decisions:
        return ""
    status_labels = {"accepted": "Принято", "rejected": "Отклонено", "discussed": "Обсуждалось"}
    lines = ["```mermaid", "flowchart LR", "  root[Обсуждение вариантов]"]
    for index, proposal in enumerate(proposals[:8], 1):
        if not isinstance(proposal, dict) or not proposal.get("text"):
            continue
        status = proposal.get("status", "discussed")
        lines.append(f"  root --> p{index}[{mermaid_text(proposal['text'])}]")
        lines.append(f"  p{index} --> ps{index}{{{status_labels.get(status, 'Обсуждалось')}}}")
    if decisions:
        lines.append("  root --> confirmed[Зафиксированные решения]")
        for index, decision in enumerate(decisions[:8], 1):
            if isinstance(decision, dict) and decision.get("text"):
                lines.append(f"  confirmed --> d{index}[{mermaid_text(decision['text'])}]")
    lines.append("```")
    return "\n".join(lines)


def gantt(data: dict) -> str:
    dated = []
    for task in data.get("tasks", []):
        if not isinstance(task, dict):
            continue
        deadline = str(task.get("deadline") or "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            dated.append(task)
    if not dated:
        return ""
    lines = ["```mermaid", "gantt", "  title План выполнения", "  dateFormat YYYY-MM-DD", "  axisFormat %d.%m", "  section Задачи"]
    for index, task in enumerate(dated[:20], 1):
        label = mermaid_text(task.get("task", "Задача"))
        lines.append(f"  {label} :milestone, task{index}, {task['deadline']}, 0d")
    lines.append("```")
    return "\n".join(lines)


def weak_from_job(transcript_json: Path) -> list[int]:
    try:
        return [int(x) for x in json.loads(transcript_json.read_text(encoding="utf-8")).get("weak_spots", [])]
    except (OSError, ValueError, TypeError):
        return []


def render_transcript(source: Path, recorded: datetime, duration: float, title: str, meeting_name: str, transcript: str,
                      weak: list[int] | None = None) -> str:
    weak_line = f'weak_spots: "{asr.format_weak(weak)}"\n' if weak else ""
    return f"""---
date: {recorded:%Y-%m-%d}
recorded_at: {recorded:%Y-%m-%dT%H:%M:%S}
time: "{recorded:%H:%M}"
type: transcript
source: PLAUD
audio_file: "{source.name}"
duration: "{format_time(duration)}"
meeting_note: "{meeting_name}"
{weak_line}---

# Стенограмма: {title}

> [!info] Запись
> **Дата:** {recorded:%d.%m.%Y}  
> **Время:** {recorded:%H:%M}  
> **Длительность:** {format_time(duration)}  
> [[../{meeting_name}|← Вернуться к карточке встречи]]

## Текст с таймкодами

{transcript}
"""


def render_sections(data: dict, template: "templates.Template | None" = None) -> str:
    """Разделы заметки по шаблону, от «Кратко» до последнего раздела шаблона."""
    template = template or templates.standard()
    blocks = []
    for section in template.sections:
        key, title = section.key, section.title
        if key == "summary":
            body = bullets(data.get("summary"))
        elif key == "map":
            body = mindmap(data)
        elif key == "flow":
            body = decision_flow(data)
            title = title if body else ""
        elif key == "plan":
            body = gantt(data)
            title = title if body else ""
        elif key == "topics":
            body = bullets(data.get("topics"))
        elif key == "decisions":
            body = fact_bullets(data.get("decisions"))
        elif key == "tasks":
            body = tasks_markdown(data.get("tasks"))
        elif key == "risks":
            body = fact_bullets(data.get("risks"))
        elif key == "open_questions":
            body = fact_bullets(data.get("open_questions"))
        elif key == "next_steps":
            body = fact_bullets(data.get("next_steps"), empty="Явно согласованные следующие шаги не зафиксированы.")
        else:
            body = fact_bullets(data.get(section.data_key))
        if title:
            blocks.append(f"## {title}\n\n{body}")
    return "\n\n".join(blocks)


def render_note(
    source: Path, recorded: datetime, duration: float, data: dict, transcript_name: str,
    template: "templates.Template | None" = None, requested: "templates.Template | None" = None, fallback: str = "",
) -> str:
    """Заметка по шаблону. `requested` и `fallback` — если выбранный шаблон не получился и применён стандартный."""
    template = template or templates.standard()
    title = slug(str(data.get("title", "")))
    meeting_id = uuid.uuid5(uuid.NAMESPACE_URL, f"plaud-local:{recorded.isoformat()}|{source.name}")
    note_lines = ""
    if requested is not None and requested.id != template.id:
        note_lines = (f"\n> [!note] Шаблон «{requested.name}» не получился\n"
                      f"> Заметка составлена по стандартному шаблону «{template.name}». Причина: {fallback or 'ответ модели не подошёл'}.\n")
    signature = f"\n_{template.signature}_\n" if template.signature else ""
    return f"""---
date: {recorded:%Y-%m-%d}
meeting_id: "{meeting_id}"
recorded_at: {recorded:%Y-%m-%dT%H:%M:%S}
time: "{recorded:%H:%M}"
type: meeting
record_type: meeting
template: "{template.id}"
source: PLAUD
source_type: Не указан
project_id: ""
audio_file: "{source.name}"
duration: "{format_time(duration)}"
status: processed
whisper_model: "{WHISPER_MODEL}"
ollama_model: "{OLLAMA_MODEL}"
analysis_version: {ANALYSIS_VERSION}
---

# {title}

> [!info] Карточка записи
> **Дата:** {recorded:%d.%m.%Y}  
> **Время:** {recorded:%H:%M}  
> **Длительность:** {format_time(duration)}  
> **Проект:** не назначен  
> **Тип:** встреча

> [!warning] Черновик AI-анализа
> Темы, решения, задачи и ответственные сформированы локальной моделью. Проверяйте важные выводы по указанным таймкодам и полной стенограмме.
{note_lines}
{render_sections(data, template)}

## Стенограмма

> [!quote]- Открыть связанную стенограмму
> [[Транскрипты/{transcript_name}|Полная стенограмма с таймкодами]]
{signature}"""


def cleanup_archive(archive_dir: Path, retention_days: int) -> int:
    if not archive_dir.exists():
        return 0
    cutoff = datetime.now().timestamp() - timedelta(days=retention_days).total_seconds()
    removed = 0
    for path in archive_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    for folder in sorted((p for p in archive_dir.rglob("*") if p.is_dir()), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass
    return removed


def spoken_segments(transcript: str, transcript_json: Path) -> list[dict]:
    """Реплики стенограммы с паузами между ними (по данным Whisper, если они есть)."""
    segments = parse_transcript(transcript)
    try:
        raw = json.loads(transcript_json.read_text(encoding="utf-8")).get("segments", [])
        spoken = [r for r in raw if str(r.get("text", "")).strip()]
        previous_end = None
        for segment, item in zip(segments, spoken):
            start, end = float(item.get("start", 0)), float(item.get("end", 0))
            segment["gap"] = None if previous_end is None else max(0.0, start - previous_end)
            previous_end = end
    except (OSError, ValueError, TypeError):
        pass
    return segments


def voice_labels(source: Path, job: Path, segments: list[dict]) -> list[str] | None:
    """Метки по голосу; None — голосовой режим недоступен или не сработал (тогда разметка по тексту).
    Отпечатки говорящих кладутся в voices.json: сервер добавит их в базу и сопоставит с людьми."""
    if os.environ.get("PLAUD_VOICE") != "1":
        return None
    try:
        from memory_desk import voice  # noqa: PLC0415
        ok, reason = voice.available()
        if not ok:
            log_event("progress", source, f"Голос пропущен: {reason}", stage="diarizing", percent=40)
            return None
        log_event("progress", source, "Определяю говорящих по голосу", stage="diarizing", percent=40)
        result = voice.diarize_by_voice(source, segments, profiles=voice.load_profiles())
    except Exception as exc:  # noqa: BLE001 — запись должна обработаться и без голоса
        log_event("progress", source, f"Голос определить не удалось: {exc}", stage="diarizing", percent=40)
        return None
    (job / "voices.json").write_text(json.dumps({"speakers": result["speakers"]}, ensure_ascii=False), encoding="utf-8")
    (job / "voices.ingested").unlink(missing_ok=True)
    return result["labels"]


def diarize_step(source: Path, job: Path, transcript: str, transcript_json: Path) -> str:
    """Оценка говорящих: по голосу (если включено и доступно), иначе по тексту. Любая неудача не мешает обработке."""
    if os.environ.get("PLAUD_DIARIZE", "1") == "0":
        return transcript
    speakers_json = job / "speakers.json"
    labels: list[str] = []
    try:
        if speakers_json.exists():
            labels = list(json.loads(speakers_json.read_text(encoding="utf-8")).get("labels", []))
        else:
            log_event("progress", source, "Определяю говорящих", stage="diarizing", percent=40)
            segments = spoken_segments(transcript, transcript_json)
            by_voice = voice_labels(source, job, segments)
            labels = by_voice if by_voice is not None else detect_speakers(segments, ollama_chat)
            speakers_json.write_text(json.dumps({"labels": labels, "mode": "voice" if by_voice is not None else "text"},
                                                ensure_ascii=False), encoding="utf-8")
    except RuntimeError as exc:
        if "не отвечает" in str(exc):
            raise
        log_event("progress", source, f"Говорящих определить не удалось: {exc}", stage="diarizing", percent=40)
        return transcript
    return relabel(transcript, labels) if labels else transcript


def choose_template(source: Path, override: str = "") -> "templates.Template":
    """Шаблон для записи: явный выбор (--template), затем выбор по файлу из очереди, затем шаблон по умолчанию."""
    wanted = override
    if not wanted:
        map_path = os.environ.get("PLAUD_TEMPLATE_MAP")
        if map_path:
            try:
                wanted = str(json.loads(Path(map_path).read_text(encoding="utf-8")).get(source.name) or "")
            except (OSError, json.JSONDecodeError, AttributeError):
                wanted = ""
    wanted = wanted or os.environ.get("PLAUD_TEMPLATE_DEFAULT", "")
    return templates.get(wanted or templates.DEFAULT_ID)


def process_file(source: Path, vault: Path, archive_dir: Path | None, force_reanalyze: bool = False,
                 template_id: str = "") -> Path:
    job = ROOT / "work" / slug(source.stem)
    job.mkdir(parents=True, exist_ok=True)
    state_path = job / "state.json"

    log_event("progress", source, "Проверяю аудиофайл", stage="checking", percent=2)
    duration = duration_seconds(source)
    if duration < 1:
        raise RuntimeError("Аудиофайл короче одной секунды.")

    normalized = job / "audio-16k.wav"
    transcript_json = job / "transcript.json"
    transcript_md = job / "transcript.md"
    analysis_json = job / "analysis.json"

    if not transcript_json.exists():
        log_event("progress", source, "Подготавливаю звук для распознавания", stage="preparing_audio", percent=6)
        prep = os.environ.get("PLAUD_ASR_PREP", "off")
        normalize_audio(source, normalized, prep if prep in asr.PREP_MODES else "off")
        log_event("progress", source, "Расшифровываю запись локально", stage="transcribing", percent=10)
        terms = dictionary.load()
        result = transcribe(normalized, terms)
        result, dropped = clean_result(result)
        result, replaced = dictionary.apply_result(result, terms)
        if replaced:
            log_event("progress", source, f"Исправлено слов по словарю терминов: {replaced}", stage="transcribing", percent=40)
        if dropped:
            log_event("progress", source, f"Убрано выдуманных или повторяющихся реплик: {len(dropped)}", stage="transcribing", percent=40)
            result["dropped_segments"] = dropped
        result["weak_spots"] = asr.weak_spots(result)
        if result["weak_spots"]:
            log_event("progress", source, f"Реплик, в которых модель сомневалась: {len(result['weak_spots'])}",
                      stage="transcribing", percent=42)
        transcript_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        text = transcript_markdown(result)
        if len(text) < 20:
            raise RuntimeError("В записи не найдено речи: возможно, там только музыка или шум.")
        transcript_md.write_text(text + "\n", encoding="utf-8")
        if os.environ.get("PLAUD_KEEP_WAV") != "1":
            normalized.unlink(missing_ok=True)  # промежуточная копия 16 кГц (~110 МБ/час) после расшифровки не нужна
    transcript = transcript_md.read_text(encoding="utf-8").strip()
    transcript = diarize_step(source, job, transcript, transcript_json)

    data = {}
    if analysis_json.exists():
        data = json.loads(analysis_json.read_text(encoding="utf-8"))
    wanted = choose_template(source, template_id)
    changed_template = bool(data) and data.get("_template_requested", templates.DEFAULT_ID) != wanted.id
    if force_reanalyze or changed_template or data.get("_analysis_version") != ANALYSIS_VERSION:
        log_event("progress", source, "Выделяю темы, решения и задачи" if wanted.id == templates.DEFAULT_ID
                  else f"Выделяю главное по шаблону «{wanted.name}»", stage="analyzing", percent=50)
        if analysis_json.exists():
            old_version = data.get("_analysis_version", 1)
            backup = job / (
                f"analysis.reanalysis-{datetime.now():%Y%m%d-%H%M%S}.json"
                if force_reanalyze else f"analysis.v{old_version}.json"
            )
            if not backup.exists():
                shutil.copy2(analysis_json, backup)
        data, used, fallback = analyze_with_template(transcript, wanted)
        data["_template_requested"] = wanted.id
        if fallback:
            data["_template_fallback"] = fallback
            log_event("progress", source, f"Шаблон «{wanted.name}» не получился ({fallback}): использован стандартный",
                      stage="analyzing", percent=80)
        analysis_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    log_event("progress", source, "Создаю заметку и стенограмму", stage="writing_note", percent=84)
    recorded = recorded_datetime(source)
    day_folder = vault / f"{recorded:%Y}" / f"{recorded:%m}" / f"{recorded:%d}"
    day_folder.mkdir(parents=True, exist_ok=True)
    note_name = f"{recorded:%Y-%m-%d %H-%M} — {slug(str(data.get('title') or source.stem))}.md"
    previous_note = None
    if state_path.exists():
        previous_state = json.loads(state_path.read_text(encoding="utf-8"))
        candidate = Path(str(previous_state.get("note", "")))
        if candidate.suffix == ".md" and candidate.is_relative_to(vault):
            previous_note = candidate
    note_path = day_folder / note_name
    if previous_note and previous_note != note_path and previous_note.exists() and not note_path.exists():
        previous_note.replace(note_path)
    transcript_folder = day_folder / "Транскрипты"
    transcript_folder.mkdir(parents=True, exist_ok=True)
    transcript_name = f"{note_path.stem} — стенограмма.md"
    transcript_path = transcript_folder / transcript_name
    transcript_note = render_transcript(
        source, recorded, duration, slug(str(data.get("title") or source.stem)), note_path.name, transcript,
        weak=weak_from_job(transcript_json),
    )
    transcript_path.write_text(transcript_note, encoding="utf-8")
    used = templates.get(data.get("_template") or templates.DEFAULT_ID)
    requested = templates.get(data.get("_template_requested") or used.id)
    note = render_note(source, recorded, duration, data, transcript_name, used, requested,
                       str(data.get("_template_fallback") or ""))
    note_path.write_text(note, encoding="utf-8")
    if note_path.stat().st_size < 200 or transcript_path.stat().st_size < 200 or "## Стенограмма" not in note:
        raise RuntimeError("Проверка готовой заметки не пройдена.")

    state_path.write_text(json.dumps({
        "source": str(source), "note": str(note_path), "transcript": str(transcript_path), "status": "completed",
        "source_hash": file_hash(source),
        "analysis_version": ANALYSIS_VERSION,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if archive_dir:
        log_event("progress", source, "Перемещаю исходную запись в временный архив", stage="archiving", percent=96)
        target = archive_dir / f"{datetime.now():%Y-%m-%d}" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise RuntimeError(f"Архив уже содержит файл: {target.name}")
        shutil.move(str(source), str(target))
        os.utime(target, None)
    return note_path


def main() -> int:
    global CURRENT_JOB_ID
    parser = argparse.ArgumentParser(description="Локальная обработка записей PLAUD")
    parser.add_argument("files", nargs="*", type=Path, help="Аудиофайлы; по умолчанию — все из inbox")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT, help="Папка заметок Obsidian")
    parser.add_argument("--archive-dir", type=Path, help="Папка временного хранения успешно обработанного аудио")
    parser.add_argument("--retention-days", type=int, default=14, help="Срок хранения обработанного аудио")
    parser.add_argument("--cleanup-only", action="store_true", help="Только очистить архив по сроку хранения")
    parser.add_argument("--reanalyze", action="store_true", help="Повторно выполнить AI-анализ")
    parser.add_argument("--job-id", default="", help="Идентификатор фонового задания")
    parser.add_argument("--template", default="", help="Идентификатор шаблона заметки (по умолчанию — из настроек)")
    args = parser.parse_args()
    CURRENT_JOB_ID = args.job_id

    if args.archive_dir:
        archive_dir = args.archive_dir.expanduser().resolve()
        removed = cleanup_archive(archive_dir, max(1, args.retention_days))
        if removed:
            print(f"Удалено архивных аудиофайлов старше {args.retention_days} дней: {removed}")
    else:
        archive_dir = None
    if args.cleanup_only:
        return 0
    files = args.files or sorted(p for p in (ROOT / "inbox").iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS)
    if not files:
        print(f"Нет аудиофайлов. Положите запись PLAUD в {ROOT / 'inbox'}")
        return 0

    failures = 0
    for source in files:
        try:
            duplicate_note = None if args.reanalyze else completed_duplicate(source.resolve())
            if duplicate_note:
                print(f"Пропущено как дубликат: {source.name} → {duplicate_note}")
                log_event("duplicate_skipped", source, note=duplicate_note)
                continue
            print(f"Обрабатываю: {source.name}", flush=True)
            log_event("started", source, "Начинаю обработку", stage="checking", percent=1)
            note = process_file(source.resolve(), args.vault.expanduser().resolve(), archive_dir, args.reanalyze, args.template)
            print(f"Готово: {note}")
            log_event("completed", source, "Запись готова", note=note, stage="completed", percent=100)
        except Exception as exc:
            failures += 1
            print(f"Ошибка для {source.name}: {exc}", file=sys.stderr)
            log_event("error", source, detail=str(exc), stage="error", percent=100)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
