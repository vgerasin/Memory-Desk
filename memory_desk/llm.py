"""Обращение к локальной Ollama: запрос JSON, устойчивый разбор обрезанных и «грязных» ответов."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path


def _repair_json(text: str):
    """Достраивает обрезанный JSON: закрывает строки и скобки, отбрасывает хвост."""
    text = text.strip()
    for _ in range(300):
        stack, in_str, esc = [], False, False
        for ch in text:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        candidate = text + ('"' if in_str else "")
        candidate = re.sub(r"[,\s]+$", "", candidate)
        if candidate.endswith(":"):
            candidate += " null"
        candidate += "".join(reversed(stack))
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            cut = text.rfind(",")
            if cut <= 0:
                return None
            text = text[:cut]
    return None


def extract_json(content: str):
    text = re.sub(r"<think>.*?(</think>|$)", "", content or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    first = text.find("{")
    if first < 0:
        return None
    last = text.rfind("}")
    if last > first:
        try:
            data = json.loads(text[first:last + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    data = _repair_json(text[first:])
    return data if isinstance(data, dict) else None


def ollama_json(
    prompt: str, *, model: str, url: str, num_ctx: int = 8192, num_predict: int = 3072,
    attempts: int = 3, timeout: int = 900, bad_response_path: Path | None = None,
) -> dict:
    last_content, last_reason = "", ""
    for attempt in range(attempts):
        payload = {
            "model": model, "stream": False, "think": False, "format": "json",
            "messages": [{"role": "user", "content": prompt}],
            "options": {
                "temperature": 0.1 if attempt == 0 else 0.3,
                "num_ctx": num_ctx, "num_predict": num_predict, "repeat_penalty": 1.1,
            },
        }
        request = urllib.request.Request(
            f"{url}/api/chat", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.load(response)
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError("Ollama не отвечает. Запустите приложение Ollama или `ollama serve`.") from exc
        last_content = response_data.get("message", {}).get("content", "")
        last_reason = str(response_data.get("done_reason", ""))
        data = extract_json(last_content)
        if data is not None:
            return data
    if bad_response_path:
        try:
            bad_response_path.parent.mkdir(parents=True, exist_ok=True)
            bad_response_path.write_text(f"done_reason: {last_reason}\n\n{last_content}", encoding="utf-8")
        except OSError:
            pass
    raise RuntimeError("Модель вернула неразборчивый ответ (Ollama вернул некорректный JSON).")
