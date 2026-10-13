"""Этап 10: языки интерфейса. Словари на 4 языка согласованы, сервер хранит язык, а надписи сервера покрыты переводами."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "frontend" / "src" / "locales"
LANGS = ("en", "fr", "de", "es")
CYR = re.compile(r"[А-Яа-яЁё]")
HOLES = re.compile(r"\{\d+\}")


def load(lang: str) -> dict:
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def holes(text: str) -> list[str]:
    return sorted(HOLES.findall(text))


# --------------------------------------------------------------------------- словари

def test_dictionaries_have_same_keys():
    keysets = {lang: set(load(lang)) for lang in LANGS}
    base = keysets["en"]
    assert len(base) > 1000
    for lang in LANGS:
        assert keysets[lang] == base, lang


@pytest.mark.parametrize("lang", LANGS)
def test_dictionary_entries_are_sound(lang):
    for key, value in load(lang).items():
        assert CYR.search(key), key
        if isinstance(value, list):
            assert key.count("|") == 2 and len(value) == 2 and all(isinstance(v, str) and v for v in value), key
            continue
        assert isinstance(value, str) and value.strip(), key
        assert holes(key) == holes(value), (lang, key, value)
        # русский остаётся только в именах файлов и папок, которые так и называются на диске
        if CYR.search(value):
            assert re.search(r"Сбросить PIN\.command|Люди/", value) or "\n" in key, (lang, value)


def test_confirm_words_are_accepted_by_server(client, monkeypatch):
    from memory_desk import erase
    from memory_desk.app import create_app  # noqa: F401
    meeting_id = client.get("/api/meetings").json()["items"][0]["id"]
    monkeypatch.setattr(erase, "_busy", lambda: True)  # слово принято, а стирание остановлено ещё до первого шага
    for lang in LANGS:
        word = load(lang)["СТЕРЕТЬ"]
        assert word in erase.WORDS
        r = client.post(f"/api/meetings/{meeting_id}/erase", json={"confirm": f" {word.lower()} "})
        assert r.status_code == 409, (lang, r.text)
    bad = client.post(f"/api/meetings/{meeting_id}/erase", json={"confirm": "DELETE"})
    assert bad.status_code == 400


# --------------------------------------------------------------------------- язык в настройках

def test_ui_lang_setting_roundtrip(client):
    s = client.get("/api/settings").json()
    assert s["ui_lang"] == "ru"
    body = {k: v for k, v in s.items() if k != "exists"}
    for lang in ("en", "fr", "de", "es", "ru"):
        body["ui_lang"] = lang
        assert client.put("/api/settings", json=body).status_code == 200
        assert client.get("/api/settings").json()["ui_lang"] == lang
    body["ui_lang"] = "xx"
    r = client.put("/api/settings", json=body)
    assert r.status_code == 400 and "языка интерфейса" in r.json()["detail"]
    assert client.get("/api/settings").json()["ui_lang"] == "ru"
    # поле можно не присылать: язык остаётся прежним
    body["ui_lang"] = "de"
    client.put("/api/settings", json=body)
    body.pop("ui_lang")
    assert client.put("/api/settings", json=body).status_code == 200
    assert client.get("/api/settings").json()["ui_lang"] == "de"
    body["ui_lang"] = "ru"
    client.put("/api/settings", json=body)


# --------------------------------------------------------------------------- надписи сервера

# поля ответов, в которых сервер присылает готовые надписи и сообщения (то же, что SERVER_TEXT_KEYS в i18n.ts)
TEXT_KEYS = {"detail", "message", "statusLabel", "kindLabel", "reasons", "warnings", "headline", "hint", "note", "problems", "state_text",
             "actorLabel", "object", "resultLabel", "empty", "reason", "dependenciesReason", "value", "warning"}
# содержимое фикстуры, а не надписи интерфейса
CONTENT = ("Qdrant",)


def is_name(key: str, text: str) -> bool:
    """Имена людей в журнале (`object`) — содержимое, а не надпись."""
    return key == "object" and not re.search(r"запис|задач|решени|закреплённ|предложени|проект|человек", text)


def known(text: str, exact: set[str], patterns: list[re.Pattern]) -> bool:
    return text in exact or any(p.match(text) for p in patterns)


def compile_patterns(keys) -> list[re.Pattern]:
    out = []
    for key in keys:
        if HOLES.search(key):
            source = "".join("(.+?)" if re.fullmatch(r"\{\d+\}", part) else re.escape(part) for part in re.split(r"(\{\d+\})", key))
            out.append(re.compile("^" + source + "$", re.S))
    return out


def test_server_labels_are_covered_by_translations(client):
    en = load("en")
    exact, patterns = set(en), compile_patterns(en)
    meetings = client.get("/api/meetings?limit=100").json()["items"]
    mid = meetings[0]["id"]
    people = client.get("/api/people").json()["items"]
    paths = ["/api/state", "/api/meta", "/api/today", "/api/tasks?kind=tasks&limit=100", "/api/tasks?kind=decisions&limit=100", "/api/projects", "/api/people",
             "/api/analytics", "/api/settings", "/api/templates", "/api/jobs", "/api/storage", "/api/audit", "/api/morning", "/api/prep",
             "/api/decisions/tree", "/api/lock/status", "/api/suggestions", "/api/voice", "/api/system", "/api/projects/kxd", f"/api/people/{people[0]['id']}",
             f"/api/meetings/{mid}/suggestions", f"/api/meetings/{mid}/series", f"/api/meetings/{mid}/rebuild"]
    paths += [f"/api/meetings/{m['id']}/erase/plan" for m in meetings] + [f"/api/meetings/{m['id']}" for m in meetings]
    missing: dict[str, set[str]] = {}

    def walk(node, key, path):
        if isinstance(node, str):
            if key in TEXT_KEYS and CYR.search(node) and not node.startswith("«") and not is_name(key, node) and not known(node, exact, patterns):
                missing.setdefault(path, set()).add(f"{key}: {node[:100]}")
        elif isinstance(node, list):
            for item in node:
                walk(item, key, path)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, k, path)

    for path in paths:
        r = client.get(path)
        if r.status_code == 200:
            walk(r.json(), "", path)
    assert not missing, json.dumps({k: sorted(v) for k, v in missing.items()}, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- исходники интерфейса

@pytest.mark.skipif(shutil.which("node") is None or not (ROOT / "frontend" / "node_modules").exists(), reason="нужны node и frontend/node_modules")
def test_frontend_i18n_checks():
    for script in (["scripts/i18n-scan.mjs", "--check"], ["scripts/i18n-check.mjs"]):
        r = subprocess.run(["node", *script], cwd=ROOT / "frontend", capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
