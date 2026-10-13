"""Этап 3.1: библиотека шаблонов заметки."""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

TRANSCRIPT = (
    "[00:00:05] **Спикер 1:** Начнём. Расскажите, как вы пришли в проект.\n\n"
    "[00:00:20] **Спикер 2:** Я пришёл два года назад, и сразу понял: данные нужно собирать в одном месте.\n\n"
    "[00:01:10] **Спикер 1:** Что мешает сейчас?\n\n"
    "[00:01:30] **Спикер 2:** Нет единого справочника клиентов. Иван подготовит выгрузку до пятницы.\n\n"
    "[00:02:00] **Спикер 1:** Решили: справочник ведём в КХД.\n"
)


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    # модули приложения читают окружение при импорте, поэтому импортируем их только после `fixture_dir`
    sys.path.insert(0, str(ROOT))
    import meetings_process, memory_desk.knowledge, memory_desk.storage, memory_desk.templates  # noqa: F401
    g = globals()
    g["mp"], g["knowledge"], g["storage"], g["templates"] = (
        meetings_process, memory_desk.knowledge, memory_desk.storage, memory_desk.templates)


@pytest.fixture()
def user_dir(tmp_path, monkeypatch):
    folder = tmp_path / "user-templates"
    monkeypatch.setenv("PLAUD_TEMPLATES_DIR", str(folder))
    return folder


# --------------------------------------------------------------------------- формат файла

def test_bundled_templates_are_valid(user_dir):
    found, problems = templates.load_all()
    assert problems == []
    assert {"meeting", "standup", "one_on_one", "interview", "lecture"} <= set(found)
    assert all(t.builtin for t in found.values())
    assert all(t.has("summary") for t in found.values())


def test_standard_titles_match_what_the_cabinet_reads():
    assert templates.STANDARD["decisions"] == knowledge.DECISION_SECTION
    assert templates.STANDARD["tasks"] == knowledge.ITEM_SECTIONS["tasks"]
    for key in ("summary", "topics", "tasks", "risks", "next_steps"):
        assert templates.STANDARD[key] in knowledge.OVERVIEW_SECTIONS
    assert templates.STANDARD["open_questions"] == "Открытые вопросы"


def test_parser_matches_pyyaml_on_shipped_files():
    yaml = pytest.importorskip("yaml")
    for path in sorted((ROOT / "templates").glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert templates.parse_yaml(text) == yaml.safe_load(text), path.name


def test_parser_subset():
    text = """
# комментарий
name: 'Тест ''один'''
note: "строка: с двоеточием"
list: [a, "b, c", 3]
block: |
  первая
  вторая

  после пустой
folded: >
  одна
  строка
items:
  - key: summary
    instruction: Кратко # хвост
  - plain
"""
    data = templates.parse_yaml(text)
    assert data["name"] == "Тест 'один'"
    assert data["note"] == "строка: с двоеточием"
    assert data["list"] == ["a", "b, c", 3]
    assert data["block"] == "первая\nвторая\n\nпосле пустой"
    assert data["folded"] == "одна строка"
    assert data["items"] == [{"key": "summary", "instruction": "Кратко"}, "plain"]


@pytest.mark.parametrize("text, fragment", [
    ("name: A\nsections:\n  - key: topics\n", "summary"),
    ("sections:\n  - key: summary\n", "названия"),
    ("name: A\nsections:\n  - key: summary\n  - key: summary\n", "дважды"),
    ("name: A\nsections:\n  - key: summary\n  - key: quotes\n", "заголовок"),
    ("name: A\nsections:\n  - key: summary\n  - key: q\n    title: Задачи\n", "занят"),
    ("name: A\nsections:\n  - key: summary\n  - key: q\n    title: стенограмма\n", "занят"),
    ("name: A\nsections:\n  - key: Сводка\n", "ключ"),
    ("name: A\nsections: [a\n", "не закрыт"),
    ("name A\n", "ключ: значение"),
])
def test_invalid_templates_are_rejected(text, fragment):
    with pytest.raises(templates.TemplateError) as err:
        templates.load_text("test", text)
    assert fragment in str(err.value)


def test_standard_sections_ignore_custom_titles():
    t = templates.load_text("t", "name: A\nsections:\n  - key: summary\n    title: Моя сводка\n  - key: tasks\n    title: Дела\n")
    assert [s.title for s in t.sections] == ["Кратко", "Задачи"]


def test_dump_roundtrip():
    original = templates.get("interview")
    again = templates.load_text("interview", templates.dump_yaml(original))
    assert again.sections == original.sections and again.focus == original.focus and again.record_types == original.record_types


def test_user_template_overrides_and_is_kept(user_dir):
    text = "name: Мой\nsections:\n  - key: summary\n  - key: ideas\n    title: Идеи\n    instruction: Выпиши идеи.\n"
    saved = templates.save_user("mine", text)
    assert (user_dir / "mine.yaml").is_file() and not saved.builtin
    found, _ = templates.load_all()
    assert found["mine"].free_sections[0].data_key == "x_ideas"
    # свой файл с id поставляемого шаблона заменяет его, а удаление возвращает поставляемый
    templates.save_user("interview", "name: Мои интервью\nsections:\n  - key: summary\n")
    assert templates.get("interview").name == "Мои интервью"
    assert templates.delete_user("interview") and templates.get("interview").name == "Интервью"
    assert not templates.delete_user("meeting")  # поставляемый без пользовательской копии не удаляется


def test_broken_user_file_is_reported_not_fatal(user_dir):
    user_dir.mkdir(parents=True)
    (user_dir / "broken.yaml").write_text("name: A\nsections: нет\n", encoding="utf-8")
    found, problems = templates.load_all()
    assert "broken" not in found and "meeting" in found
    assert problems and "broken.yaml" in problems[0]


def test_suggest_by_record_type(user_dir):
    assert templates.suggest("interview").id == "interview"
    assert templates.suggest("zoom").id == "meeting"
    assert templates.suggest("thought").id == "meeting"
    assert templates.suggest(None).id == "meeting"


def test_new_id_is_free_and_ascii(user_dir):
    first = templates.new_id("Разбор полётов")
    assert re.fullmatch(r"[a-z][a-z0-9-]*", first)
    templates.save_user(first, "name: X\nsections:\n  - key: summary\n")
    assert templates.new_id("Разбор полётов") != first


# --------------------------------------------------------------------------- запрос к модели

def test_default_prompt_is_unchanged():
    prompt = mp.analysis_prompt("[00:00:01] Привет")
    assert 'Особенности этой записи' not in prompt
    assert '"proposals": [{"text": "предложенный вариант", "status": "accepted|rejected|discussed", "evidence_time": "HH:MM:SS"}],' in prompt
    keys = re.findall(r'^  "(\w+)":', prompt, re.M)
    assert keys == ["title", "summary", "topics", "proposals", "decisions", "tasks", "risks", "open_questions", "next_steps"]


def test_template_prompt_lists_only_its_fields():
    interview = templates.get("interview")
    prompt = mp.analysis_prompt("[00:00:01] Привет", template=interview)
    keys = re.findall(r'^  "(\w+)":', prompt, re.M)
    assert keys == ["title", "summary", "open_questions", "next_steps", "x_qa", "x_quotes", "x_facts"]
    assert "Это интервью" in prompt and "Дословные фразы" in prompt
    assert '"decisions"' not in prompt


# --------------------------------------------------------------------------- три шаблона — три заметки

def fake_model(monkeypatch, *, empty_custom=False, calls=None):
    def answer(prompt: str) -> dict:
        if calls is not None:
            calls.append(prompt)
        base = {"title": "Разговор о справочнике", "summary": ["Говорили о едином справочнике клиентов"], "topics": ["Справочник"],
                "proposals": [{"text": "Вести справочник в КХД", "status": "accepted", "evidence_time": "00:02:00"}],
                "decisions": [{"text": "Справочник ведём в КХД", "evidence_time": "00:02:00"}],
                "tasks": [{"task": "Подготовить выгрузку клиентов", "owner": "Иван", "deadline": "не указан", "evidence_time": "00:01:30"}],
                "risks": [], "open_questions": [{"text": "Кто владелец справочника?", "evidence_time": "00:01:10"}],
                "next_steps": []}
        if not empty_custom:
            for key in re.findall(r'^  "(x_\w+)":', prompt, re.M):
                base[key] = [{"text": f"пункт {key}", "evidence_time": "00:01:30"}]
            if "x_quotes" in prompt:
                base.update(x_qa=[{"text": "Как пришли в проект? — Два года назад", "evidence_time": "00:00:20"}],
                            x_quotes=[{"text": "Данные нужно собирать в одном месте", "evidence_time": "00:00:20"}],
                            x_facts=["Пришёл два года назад"])
        return base
    monkeypatch.setattr(mp, "ollama_chat", answer)


def sections_of(note: str) -> list[str]:
    return list(knowledge.parse_note_sections(note))


def render(template_id: str):
    data, used, fallback = mp.analyze_with_template(TRANSCRIPT, templates.get(template_id))
    recorded = datetime(2026, 10, 13, 10, 0)
    note = mp.render_note(Path("PLAUD_1.mp3"), recorded, 125, data, "x — стенограмма.md", used, templates.get(template_id), fallback)
    return note, used, fallback


def test_one_transcript_three_templates_three_forms(monkeypatch, user_dir):
    fake_model(monkeypatch)
    forms = {}
    for tid in ("meeting", "interview", "standup"):
        note, used, fallback = render(tid)
        assert used.id == tid and fallback == ""
        forms[tid] = sections_of(note)
    assert len({tuple(f) for f in forms.values()}) == 3
    assert "Яркие цитаты" in forms["interview"] and "Яркие цитаты" not in forms["meeting"]
    assert "Статус по направлениям" in forms["standup"] and "Предполагаемые решения и договорённости" not in forms["standup"]
    assert "Предполагаемые решения и договорённости" in forms["meeting"]
    assert forms["meeting"][-1] == forms["interview"][-1] == forms["standup"][-1] == "Стенограмма"


def test_registry_sections_keep_the_format_the_cabinet_reads(monkeypatch, user_dir):
    fake_model(monkeypatch)
    for tid, expects_decisions in (("meeting", True), ("standup", False), ("one_on_one", False), ("lecture", False)):
        note, _used, _ = render(tid)
        sections = knowledge.parse_note_sections(note)
        tasks = storage.bullet_lines(sections["Задачи"])
        fact = storage.parse_fact(tasks[0])
        assert fact["text"] == "Подготовить выгрузку клиентов" and fact["owner"] == "Иван" and fact["evidence_time"] == "00:01:30"
        assert re.search(r"(?m)^- \[ \] ", sections["Задачи"])
        assert ("Предполагаемые решения и договорённости" in sections) is expects_decisions
    note, _used, _ = render("meeting")
    decisions = storage.bullet_lines(knowledge.parse_note_sections(note)["Предполагаемые решения и договорённости"])
    assert storage.parse_fact(decisions[0])["evidence_time"] == "00:02:00"


def test_custom_sections_have_timecodes_and_empty_marker(monkeypatch, user_dir):
    fake_model(monkeypatch)
    note, *_ = render("interview")
    sections = knowledge.parse_note_sections(note)
    assert "Данные нужно собирать в одном месте — `⏱ 00:00:20`" in sections["Яркие цитаты"]
    assert "- Пришёл два года назад" in sections["Ключевые факты и цифры"]
    assert "template: \"interview\"" in note
    # пустой свой раздел не ломает заметку, а подставляет маркер, который кабинет не считает пунктом
    data = {"title": "T", "summary": ["a"], "x_qa": [], "x_quotes": [{"text": "q", "evidence_time": ""}], "x_facts": []}
    text = mp.render_sections(data, templates.get("interview"))
    assert "## Вопросы и ответы\n\nНе выявлено." in text
    assert storage.bullet_lines("Не выявлено.") == []


def test_signature_and_free_titles(monkeypatch, user_dir):
    templates.save_user("signed", "name: С подписью\nsignature: Составил секретарь\nsections:\n  - key: summary\n  - key: ideas\n    title: Идеи\n")
    monkeypatch.setattr(mp, "ollama_chat", lambda prompt: {"title": "T", "summary": ["a"], "x_ideas": "- идея один\n- идея два"})
    note, used, _ = render("signed")
    assert note.rstrip().endswith("_Составил секретарь_")
    assert "- идея один\n- идея два" in knowledge.parse_note_sections(note)["Идеи"]
    assert used.id == "signed"


def test_chunked_transcript_merges_custom_sections(monkeypatch, user_dir):
    monkeypatch.setattr(mp, "CHUNK_CHARS", 200)
    fake_model(monkeypatch)
    data, used, fallback = mp.analyze_with_template(TRANSCRIPT, templates.get("interview"))
    assert used.id == "interview" and not fallback
    assert [f["text"] for f in data["x_quotes"]] == ["Данные нужно собирать в одном месте"]  # одинаковые пункты не дублируются


# --------------------------------------------------------------------------- откат к стандартному шаблону

def test_fallback_when_custom_sections_are_empty(monkeypatch, user_dir):
    fake_model(monkeypatch, empty_custom=True)
    note, used, fallback = render("interview")
    assert used.id == "meeting" and "собственных разделов" in fallback
    sections = knowledge.parse_note_sections(note)
    assert "Задачи" in sections and "Яркие цитаты" not in sections
    assert 'template: "meeting"' in note
    assert "Шаблон «Интервью» не получился" in note


def test_fallback_when_model_answer_is_unparseable(monkeypatch, user_dir):
    calls = []

    def flaky(prompt):
        calls.append(prompt)
        if "x_quotes" in prompt:
            raise RuntimeError("Модель вернула неразборчивый ответ (Ollama вернул некорректный JSON).")
        return {"title": "T", "summary": ["a"]}
    monkeypatch.setattr(mp, "ollama_chat", flaky)
    data, used, fallback = mp.analyze_with_template(TRANSCRIPT, templates.get("interview"))
    assert used.id == "meeting" and "неразборчивый" in fallback and len(calls) == 2


def test_unavailable_model_is_not_hidden_by_fallback(monkeypatch, user_dir):
    def down(prompt):
        raise RuntimeError("Ollama не отвечает. Запустите приложение Ollama.")
    monkeypatch.setattr(mp, "ollama_chat", down)
    with pytest.raises(RuntimeError, match="не отвечает"):
        mp.analyze_with_template(TRANSCRIPT, templates.get("interview"))


def test_default_template_has_no_validation_or_fallback(monkeypatch, user_dir):
    monkeypatch.setattr(mp, "ollama_chat", lambda prompt: {"title": "T", "summary": []})
    data, used, fallback = mp.analyze_with_template(TRANSCRIPT, templates.get("meeting"))
    assert used.id == "meeting" and fallback == ""  # как и раньше: пустая заметка лучше цикла повторов


def test_standard_note_matches_old_layout(monkeypatch):
    fake_model(monkeypatch)
    note, *_ = render("meeting")
    assert sections_of(note) == ["Кратко", "Карта встречи", "Схема решений", "Основные темы",
                                 "Предполагаемые решения и договорённости", "Задачи", "Открытые вопросы", "Риски",
                                 "Следующие шаги", "Стенограмма"]


def test_choose_template_priority(tmp_path, monkeypatch, user_dir):
    mapping = tmp_path / "map.json"
    mapping.write_text('{"a.mp3": "lecture"}', encoding="utf-8")
    monkeypatch.setenv("PLAUD_TEMPLATE_MAP", str(mapping))
    monkeypatch.setenv("PLAUD_TEMPLATE_DEFAULT", "standup")
    assert mp.choose_template(Path("a.mp3")).id == "lecture"
    assert mp.choose_template(Path("b.mp3")).id == "standup"
    assert mp.choose_template(Path("a.mp3"), "interview").id == "interview"
    monkeypatch.setenv("PLAUD_TEMPLATE_DEFAULT", "нет-такого")
    assert mp.choose_template(Path("b.mp3")).id == "meeting"


# --------------------------------------------------------------------------- API

def test_templates_api_lifecycle(client, user_dir):
    data = client.get("/api/templates").json()
    ids = [t["id"] for t in data["items"]]
    assert ids[0] == "meeting" and {"standup", "one_on_one", "interview", "lecture"} <= set(ids)
    assert data["default"] == "meeting"
    interview = next(t for t in data["items"] if t["id"] == "interview")
    assert [s["key"] for s in interview["sections"]][:3] == ["summary", "qa", "quotes"]
    text = client.get("/api/templates/interview").json()["text"]
    assert "Яркие цитаты" in text

    body = "name: Разбор\nsections:\n  - key: summary\n  - key: lessons\n    title: Уроки\n    instruction: Что мы поняли.\n"
    saved = client.put("/api/templates/postmortem", json={"text": body})
    assert saved.status_code == 200 and saved.json()["saved"]["builtin"] is False
    assert "postmortem" in [t["id"] for t in saved.json()["items"]]
    assert client.put("/api/templates/postmortem", json={"text": "name: A\n"}).status_code == 400
    assert client.put("/api/templates/Bad Id", json={"text": body}).status_code in (400, 404)
    assert client.delete("/api/templates/meeting").status_code == 400
    assert client.delete("/api/templates/postmortem").status_code == 200
    assert client.get("/api/templates/postmortem").status_code == 404


def test_default_template_setting(client, user_dir):
    cfg = client.get("/api/settings").json()
    body = {k: cfg[k] for k in cfg if k != "exists"}
    assert client.put("/api/settings", json={**body, "default_template": "нет"}).status_code == 400
    ok = client.put("/api/settings", json={**body, "default_template": "lecture"})
    assert ok.status_code == 200 and client.get("/api/templates").json()["default"] == "lecture"
    assert client.put("/api/settings", json={**body, "default_template": "meeting"}).status_code == 200


def test_draft_from_description(client, user_dir, monkeypatch):
    from memory_desk import llm
    seen = []

    def fake(prompt, **kwargs):
        seen.append(prompt)
        return {"name": "Разбор полётов", "description": "Что пошло не так", "focus": "Это разбор инцидента.",
                "sections": [{"key": "summary", "instruction": "Что случилось"},
                             {"key": "causes", "title": "Причины", "instruction": "Почему так вышло"},
                             {"key": "stenogramma", "title": "Стенограмма"},      # служебный заголовок — отбрасывается
                             {"key": "decisions"}, {"key": "map"}, {"key": "Bad Key"}]}
    monkeypatch.setattr(llm, "ollama_json", fake)
    reply = client.post("/api/templates/draft", json={"description": "Разбор инцидента на продакшене с причинами и выводами"})
    assert reply.status_code == 200
    draft = reply.json()
    assert draft["name"] == "Разбор полётов" and re.fullmatch(r"[a-z][a-z0-9-]*", draft["id"])
    assert [s["key"] for s in draft["sections"]] == ["summary", "causes", "decisions"]   # схемы модель не добавляет
    assert "Причины" in draft["text"] and "Разбор инцидента" in seen[0]
    assert not (user_dir / f"{draft['id']}.yaml").exists()  # предпросмотр ничего не сохраняет
    assert client.put(f"/api/templates/{draft['id']}", json={"text": draft["text"]}).status_code == 200
    assert client.post("/api/templates/draft", json={"description": "коротко"}).status_code == 422
    monkeypatch.setattr(llm, "ollama_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Ollama не отвечает")))
    assert client.post("/api/templates/draft", json={"description": "Разбор инцидента на продакшене"}).status_code == 503


def test_file_template_choice_in_queue(client, user_dir, tmp_path):
    cfg = client.get("/api/settings").json()
    folder = Path(cfg["recordings_dir"])
    audio = folder / "stage3-choice.mp3"
    audio.write_bytes(b"\x00" * 64)
    try:
        client.app.state.watcher.tick()
        queue = client.get("/api/jobs").json()["jobs"]
        item = next(j for j in queue if j["file"] == audio.name)
        assert item["templateId"] == "meeting"
        assert client.post("/api/jobs/template", json={"file": audio.name, "templateId": "interview"}).json()["templateId"] == "interview"
        client.app.state.watcher.tick()
        assert next(j for j in client.get("/api/jobs").json()["jobs"] if j["file"] == audio.name)["templateId"] == "interview"
        assert client.post("/api/jobs/template", json={"file": audio.name, "templateId": "нет"}).status_code == 404
        assert client.post("/api/jobs/template", json={"file": "../x.mp3", "templateId": "lecture"}).status_code in (400, 404)
        assert client.post("/api/jobs/template", json={"file": audio.name, "templateId": ""}).json()["templateId"] == "meeting"
    finally:
        audio.unlink(missing_ok=True)
        from memory_desk import jobs
        jobs.set_file_template(audio.name, "")


# --------------------------------------------------------------------------- пересборка готовой записи

class Note:
    """Заметка со стенограммой в хранилище фикстуры, убирается после теста."""

    def __init__(self, client):
        self.client = client
        self.vault = Path(client.get("/api/settings").json()["obsidian_dir"])
        self.folder = self.vault / "tmpl-test"
        self.recorded = (datetime.now() - timedelta(days=3)).replace(hour=11, minute=0, second=0, microsecond=0)
        self.title = "Разговор о справочнике"

    def create(self):
        (self.folder / "Транскрипты").mkdir(parents=True, exist_ok=True)
        stem = f"{self.recorded:%Y-%m-%d %H-%M} — {self.title}"
        transcript_name = f"{stem} — стенограмма.md"
        data = {"title": self.title, "summary": ["Старая заметка"], "topics": [], "proposals": [],
                "decisions": [{"text": "Справочник ведём в КХД", "evidence_time": "00:02:00"}],
                "tasks": [{"task": "Подготовить выгрузку клиентов", "owner": "Иван", "deadline": "не указан", "evidence_time": "00:01:30"},
                          {"task": "Старая задача, которой нет в стенограмме", "owner": "Анна", "deadline": "не указан", "evidence_time": "00:01:40"}],
                "risks": [], "open_questions": [], "next_steps": []}
        note = mp.render_note(Path("PLAUD_tmpl.mp3"), self.recorded, 125, data, transcript_name)
        note = note.replace('project_id: ""', 'project_id: "kxd"\nproject: "КХД"').replace("source_type: Не указан", "source_type: Интервью")
        note = note.replace('record_type: meeting', 'record_type: interview')
        self.path = self.folder / f"{stem}.md"
        self.path.write_text(note, encoding="utf-8")
        (self.folder / "Транскрипты" / transcript_name).write_text(
            mp.render_transcript(Path("PLAUD_tmpl.mp3"), self.recorded, 125, self.title, self.path.name, TRANSCRIPT.strip()),
            encoding="utf-8")
        self.client.app.state.core.reload(force=True)
        items = self.client.get("/api/meetings", params={"limit": 200}).json()["items"]
        self.id = next(m["id"] for m in items if m["title"] == self.title)
        return self

    def remove(self):
        import shutil
        shutil.rmtree(self.folder, ignore_errors=True)
        self.client.app.state.core.reload(force=True)


@pytest.fixture()
def note(client, user_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_DESK_DATA_DIR", str(tmp_path))  # копии заметок уходят во временную папку
    from memory_desk import config, rebuild
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    created = Note(client).create()
    yield created
    created.remove()
    rebuild.STATE.clear()


def wait_rebuild(client, meeting_id):
    for _ in range(100):
        state = client.get(f"/api/meetings/{meeting_id}/rebuild").json()
        if state["state"] in {"done", "error"}:
            return state
        time.sleep(0.05)
    raise AssertionError("пересборка не закончилась")


def test_record_reports_template_and_suggestion(client, note):
    detail = client.get(f"/api/meetings/{note.id}").json()
    assert detail["template"] == {"id": "meeting", "name": "Совещание с решениями", "suggestedId": "interview"}
    assert detail["extraSections"] == []


def test_rebuild_with_other_template(client, note, monkeypatch, tmp_path):
    fake_model(monkeypatch)
    # пользователь уже подтвердил одну задачу и закрыл другую
    tasks = {t["text"]: t for t in client.get(f"/api/meetings/{note.id}").json()["tasks"]}
    assert client.patch(f"/api/tasks/{tasks['Подготовить выгрузку клиентов']['id']}", json={"status": "open", "owner": "Иван Петров"}).status_code == 200
    assert client.patch(f"/api/tasks/{tasks['Старая задача, которой нет в стенограмме']['id']}", json={"status": "done"}).status_code == 200
    before = note.path.read_text(encoding="utf-8")

    start = client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "lecture"})
    assert start.status_code == 200
    state = wait_rebuild(client, note.id)
    assert state["state"] == "done", state
    assert state["templateId"] == "lecture" and state["keptTasks"] == 2

    after = note.path.read_text(encoding="utf-8")
    sections = knowledge.parse_note_sections(after)
    assert 'template: "lecture"' in after
    assert 'project_id: "kxd"' in after and 'record_type: interview' in after   # метаданные не тронуты
    assert "Старая заметка" not in after and "Ключевые тезисы" in sections
    assert list(sections)[-1] == "Стенограмма" and "[[Транскрипты/" in sections["Стенограмма"]
    assert re.search(r"(?m)^# Разговор о справочнике$", after)
    # подтверждённые задачи и их состояние сохранились, и в заметке, и в реестре
    assert "Иван Петров" in sections["Задачи"] and re.search(r"(?m)^- \[x\] Старая задача", sections["Задачи"])
    registry = {t["text"]: t for t in client.get(f"/api/meetings/{note.id}").json()["tasks"]}
    assert registry["Подготовить выгрузку клиентов"]["status"] == "open" and registry["Старая задача, которой нет в стенограмме"]["done"]
    assert len([t for t in registry if t == "Подготовить выгрузку клиентов"]) == 1
    # старая заметка сохранена
    backups = list((tmp_path / "note-backups").glob("*.md"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == before
    # теперь запись показывает шаблон и свои разделы
    detail = client.get(f"/api/meetings/{note.id}").json()
    assert detail["template"]["id"] == "lecture"
    assert {s["title"] for s in detail["extraSections"]} == {"Ключевые тезисы", "Термины и определения", "Примеры и кейсы"}


def test_rebuild_replaces_drafts_and_keeps_registry_in_sync(client, note, monkeypatch):
    fake_model(monkeypatch)
    before = {t["text"] for t in client.get(f"/api/meetings/{note.id}").json()["tasks"]}
    assert before == {"Подготовить выгрузку клиентов", "Старая задача, которой нет в стенограмме"}  # оба — черновики
    assert client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "meeting"}).status_code == 200
    assert wait_rebuild(client, note.id)["state"] == "done"
    detail = client.get(f"/api/meetings/{note.id}").json()
    assert {t["text"] for t in detail["tasks"]} == {"Подготовить выгрузку клиентов"}   # устаревший черновик заменён
    assert [d["text"] for d in detail["decisions"]] == ["Справочник ведём в КХД"]
    assert "Старая задача" not in note.path.read_text(encoding="utf-8")


def test_rebuild_fallback_is_reported(client, note, monkeypatch):
    fake_model(monkeypatch, empty_custom=True)
    assert client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "interview"}).status_code == 200
    state = wait_rebuild(client, note.id)
    assert state["state"] == "done" and state["templateId"] == "meeting" and state["requestedId"] == "interview"
    assert "собственных разделов" in state["fallback"]
    text = note.path.read_text(encoding="utf-8")
    assert "Шаблон «Интервью» не получился" in text
    # повторная пересборка не копит предупреждения
    assert client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "interview"}).status_code == 200
    wait_rebuild(client, note.id)
    assert note.path.read_text(encoding="utf-8").count("не получился") == 1


def test_rebuild_errors(client, note, monkeypatch):
    assert client.post("/api/meetings/нет-такой/rebuild", json={"templateId": "lecture"}).status_code == 404
    assert client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "нет"}).status_code == 400
    monkeypatch.setattr(mp, "ollama_chat", lambda prompt: (_ for _ in ()).throw(RuntimeError("Ollama не отвечает. Запустите Ollama.")))
    before = note.path.read_text(encoding="utf-8")
    assert client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "lecture"}).status_code == 200
    state = wait_rebuild(client, note.id)
    assert state["state"] == "error" and "Ollama" in state["message"]
    assert note.path.read_text(encoding="utf-8") == before   # при ошибке заметка не тронута
    assert client.get("/api/meetings/нет/rebuild").json() == {"state": "idle"}


def test_custom_sections_reach_search_index(client, note, monkeypatch):
    fake_model(monkeypatch)
    assert client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "interview"}).status_code == 200
    assert wait_rebuild(client, note.id)["state"] == "done"
    meeting = client.app.state.core.by_id[note.id]
    chunks = knowledge.sections(meeting)
    quotes = [c for c in chunks if c["section"] == "Яркие цитаты"]
    assert quotes and "в одном месте" in quotes[0]["text"] and quotes[0]["timestamp"] == "00:00:20"


def test_rebuild_without_registry_sections_drops_drafts_but_keeps_confirmed(client, note, monkeypatch):
    fake_model(monkeypatch)
    tasks = {t["text"]: t for t in client.get(f"/api/meetings/{note.id}").json()["tasks"]}
    assert client.patch(f"/api/tasks/{tasks['Подготовить выгрузку клиентов']['id']}", json={"status": "open"}).status_code == 200
    assert client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "interview"}).status_code == 200
    state = wait_rebuild(client, note.id)
    assert state["state"] == "done" and state["templateId"] == "interview" and state["keptTasks"] == 1
    detail = client.get(f"/api/meetings/{note.id}").json()
    assert [t["text"] for t in detail["tasks"]] == ["Подготовить выгрузку клиентов"]   # черновик удалён, подтверждённая осталась
    assert detail["decisions"] == []                                                  # черновик решения удалён
    sections = knowledge.parse_note_sections(note.path.read_text(encoding="utf-8"))
    assert "Задачи" not in sections and "Яркие цитаты" in sections
