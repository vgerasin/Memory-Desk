"""Этап 3.1: библиотека шаблонов заметки. Этап 3.2: взгляды на запись."""
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
    import meetings_process, memory_desk.knowledge, memory_desk.storage, memory_desk.templates, memory_desk.views  # noqa: F401
    g = globals()
    g["mp"], g["knowledge"], g["storage"], g["templates"], g["views"] = (
        meetings_process, memory_desk.knowledge, memory_desk.storage, memory_desk.templates, memory_desk.views)


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


# --------------------------------------------------------------------------- ход составления протокола

def test_protocol_progress_reports_stages_and_result(client, items, monkeypatch):
    from memory_desk import digest, materials
    import threading
    gate, entered = threading.Event(), threading.Event()

    def slow(prompt, num_predict=700):
        entered.set()
        assert gate.wait(5)
        return "Обсудили архитектуру и договорились о сроках."
    monkeypatch.setattr(digest, "generate", slow)
    detail = next(d for d in (client.get(f"/api/meetings/{m['id']}").json() for m in items.values()) if d["tasks"] and d["summary"])
    mid = detail["id"]
    assert client.get(f"/api/meetings/{mid}/protocol/progress").json() == {"state": "idle"}
    started = client.post(f"/api/meetings/{mid}/protocol/start").json()
    assert started["state"] == "running" and started["steps"] == 4 and 0 < started["percent"] < 100
    assert entered.wait(5)
    mid_run = client.get(f"/api/meetings/{mid}/protocol/progress").json()
    assert mid_run["state"] == "running" and mid_run["step"] == 2 and "модель" in mid_run["label"] and "result" not in mid_run
    assert 15 <= mid_run["percent"] < 87
    again = client.post(f"/api/meetings/{mid}/protocol/start").json()   # повторный запуск не плодит задания
    assert again["state"] == "running"
    gate.set()
    for _ in range(100):
        done = client.get(f"/api/meetings/{mid}/protocol/progress").json()
        if done["state"] != "running":
            break
        time.sleep(0.05)
    assert done["state"] == "done" and done["percent"] == 100
    assert done["result"]["mode"] == "model" and "Обсудили архитектуру" in done["result"]["markdown"]
    assert done["result"] == client.post(f"/api/meetings/{mid}/protocol").json()   # тот же протокол, что и без индикатора
    assert client.post("/api/meetings/нет/protocol/start").status_code == 404
    assert client.get("/api/meetings/нет/protocol/progress").json() == {"state": "idle"}


def test_protocol_progress_without_model_and_percent_curve(client, items, monkeypatch):
    from memory_desk import digest, materials
    monkeypatch.setattr(digest, "generate", lambda *a, **k: (_ for _ in ()).throw(OSError("нет связи")))
    mid = next(d for d in (client.get(f"/api/meetings/{m['id']}").json() for m in items.values()) if d["summary"])["id"]
    client.post(f"/api/meetings/{mid}/protocol/start")
    for _ in range(100):
        state = client.get(f"/api/meetings/{mid}/protocol/progress").json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "done" and state["result"]["mode"] == "structured"
    # процент растёт по ходу ответа модели, но не достигает конца этапа
    job = {"state": "running", "base": 15, "span": 72, "tau": 14, "stageStarted": 100.0}
    values = [materials._percent(job, 100 + t) for t in (0, 5, 15, 40, 600)]
    assert values == sorted(values) and values[0] == 15 and values[-1] <= 87


# --------------------------------------------------------------------------- 3.2 взгляды на запись

def fake_views(monkeypatch, answer=None, calls=None):
    """Подменяет запрос модели для взглядов; `answer(prompt)` возвращает словарь ответа."""
    from memory_desk import llm

    def fake(prompt, **kwargs):
        if calls is not None:
            calls.append(prompt)
        if answer:
            return answer(prompt)
        return {"items": [{"text": "Справочник ведём в КХД", "evidence_time": "00:02:00"},
                          {"text": "Иван подготовит выгрузку до пятницы", "evidence_time": "00:01:30"}]}
    monkeypatch.setattr(llm, "ollama_json", fake)


def wait_view(client, meeting_id):
    for _ in range(100):
        state = client.get(f"/api/meetings/{meeting_id}/views/status").json()
        if state["state"] in {"done", "error"}:
            return state
        time.sleep(0.05)
    raise AssertionError("взгляд не построился")


@pytest.fixture(autouse=True)
def _clean_views():
    yield
    views.JOBS.clear()


def add_view(client, note, **body):
    response = client.post(f"/api/meetings/{note.id}/views", json=body)
    assert response.status_code == 200, response.text
    return wait_view(client, note.id)


def test_presets_listed(client):
    data = client.get("/api/views/presets").json()
    assert [p["id"] for p in data["presets"]] == ["decisions", "tasks_by_person", "client_questions", "risks", "exec5"]
    assert data["max"] == 8 and all(p["instruction"] for p in data["presets"])


def test_view_section_roundtrip():
    section = views.render_section("c-demo", "custom", "Что обсудить с -- юристом", "Выпиши вопросы юристу — сроки и \"суммы\"",
                                   [{"text": "Сроки договора", "evidence_time": "00:01:10"}], "qwen3:4b")
    assert "<!-- md-view id=c-demo kind=custom" in section
    marker = section.splitlines()[2]
    assert marker.count("--") == 2   # открытие и закрытие, внутри двойных дефисов нет
    found = views.parse_sections(section)
    assert len(found) == 1 and found[0]["title"] == "Что обсудить с -- юристом" and not found[0]["edited"]
    assert found[0]["instruction"].startswith("Выпиши вопросы юристу") and found[0]["items"][0]["t"] == "00:01:10"
    assert found[0]["model"] == "qwen3:4b"
    edited = section.replace("Сроки договора", "Сроки договора и оплата")
    assert views.parse_sections(edited)[0]["edited"] is True


def test_add_view_keeps_rest_of_note_byte_identical(client, note, monkeypatch):
    fake_views(monkeypatch)
    before = note.path.read_text(encoding="utf-8")
    state = add_view(client, note, presetId="decisions")
    assert state["state"] == "done" and state["percent"] == 100
    after = note.path.read_text(encoding="utf-8")
    assert "## Взгляд: Только решения" in after and "Справочник ведём в КХД — `⏱ 00:02:00`" in after
    assert after.index("## Взгляд: Только решения") < after.index("## Стенограмма")
    # убираем раздел взгляда — остальной текст совпадает с исходным до байта
    assert client.delete(f"/api/meetings/{note.id}/views/decisions").status_code == 200
    assert note.path.read_text(encoding="utf-8") == before
    assert client.delete(f"/api/meetings/{note.id}/views/decisions").status_code == 404


def test_view_in_record_and_not_in_extra_sections(client, note, monkeypatch):
    fake_views(monkeypatch)
    add_view(client, note, presetId="risks")
    detail = client.get(f"/api/meetings/{note.id}").json()
    assert [v["id"] for v in detail["views"]] == ["risks"]
    view = detail["views"][0]
    assert view["title"] == "Риски" and view["kind"] == "preset" and view["edited"] is False
    assert view["items"][0] == {"text": "Справочник ведём в КХД", "t": "00:02:00", "tSec": 120}
    assert all(not s["title"].startswith("Взгляд") for s in detail["extraSections"])
    # основное саммари и задачи записи не изменились
    assert detail["summary"] == ["Старая заметка"]
    assert {t["text"] for t in detail["tasks"]} == {"Подготовить выгрузку клиентов", "Старая задача, которой нет в стенограмме"}


def test_view_reaches_search_index_without_service_line(client, note, monkeypatch):
    fake_views(monkeypatch)
    add_view(client, note, presetId="exec5")
    meeting = client.app.state.core.by_id[note.id]
    chunks = [c for c in knowledge.sections(meeting) if c["section"] == "Взгляд: Для руководителя на 5 строк"]
    assert chunks and "Иван подготовит выгрузку" in chunks[0]["text"] and "md-view" not in chunks[0]["text"]
    assert chunks[0]["timestamp"] in {"00:02:00", "00:01:30"}


def test_refresh_respects_manual_edits(client, note, monkeypatch):
    fake_views(monkeypatch)
    add_view(client, note, presetId="decisions")
    text = note.path.read_text(encoding="utf-8")
    head, view = text.split("## Взгляд: Только решения")
    note.path.write_text(head + "## Взгляд: Только решения" + view.replace("Справочник ведём в КХД —", "Справочник ведём в КХД (правка Валерия) —"),
                         encoding="utf-8")
    client.app.state.core.reload(force=True)
    assert client.get(f"/api/meetings/{note.id}").json()["views"][0]["edited"] is True
    refused = client.post(f"/api/meetings/{note.id}/views/decisions/refresh")
    assert refused.status_code == 409 and "правили вручную" in refused.json()["detail"]
    assert "правка Валерия" in note.path.read_text(encoding="utf-8")   # молча не затёрто
    fake_views(monkeypatch, answer=lambda p: {"items": [{"text": "Новое решение", "evidence_time": "00:02:00"}]})
    assert client.post(f"/api/meetings/{note.id}/views/decisions/refresh", params={"force": "true"}).status_code == 200
    assert wait_view(client, note.id)["state"] == "done"
    final = note.path.read_text(encoding="utf-8")
    assert "Новое решение" in final and "правка Валерия" not in final and final.count("## Взгляд: Только решения") == 1
    assert client.get(f"/api/meetings/{note.id}").json()["views"][0]["edited"] is False


def test_refresh_of_unedited_view_needs_no_confirmation(client, note, monkeypatch):
    fake_views(monkeypatch)
    add_view(client, note, presetId="risks")
    assert client.post(f"/api/meetings/{note.id}/views/risks/refresh").status_code == 200
    assert wait_view(client, note.id)["state"] == "done"
    assert client.post(f"/api/meetings/{note.id}/views/нет/refresh").status_code == 404


def test_custom_view_and_validation(client, note, monkeypatch):
    calls = []
    fake_views(monkeypatch, calls=calls)
    assert client.post(f"/api/meetings/{note.id}/views", json={"title": "Только цифры", "instruction": "коротко"}).status_code == 400
    assert client.post(f"/api/meetings/{note.id}/views", json={"title": "", "instruction": "Выпиши все цифры и даты"}).status_code == 400
    assert client.post(f"/api/meetings/{note.id}/views", json={"presetId": "нет"}).status_code == 404
    assert client.post("/api/meetings/нет/views", json={"presetId": "risks"}).status_code == 404
    add_view(client, note, title="Что сказать юристу", instruction="Выпиши всё, что нужно согласовать с юристом")
    view = client.get(f"/api/meetings/{note.id}").json()["views"][0]
    assert view["kind"] == "custom" and view["title"] == "Что сказать юристу" and view["id"].startswith("c-")
    assert "согласовать с юристом" in view["instruction"] and "согласовать с юристом" in calls[0]
    # повторный пересчёт использует сохранённый фокус
    assert client.post(f"/api/meetings/{note.id}/views/{view['id']}/refresh").status_code == 200
    wait_view(client, note.id)
    assert "согласовать с юристом" in calls[-1]
    # то же название второй раз — это тот же взгляд
    assert client.post(f"/api/meetings/{note.id}/views", json={"title": "Что сказать юристу", "instruction": "Другой фокус тут"}).status_code == 409


def test_duplicate_and_limit(client, note, monkeypatch):
    fake_views(monkeypatch)
    add_view(client, note, presetId="risks")
    assert client.post(f"/api/meetings/{note.id}/views", json={"presetId": "risks"}).status_code == 409
    for index in range(7):
        add_view(client, note, title=f"Фокус {index}", instruction="Выпиши то, что относится к фокусу")
    over = client.post(f"/api/meetings/{note.id}/views", json={"presetId": "decisions"})
    assert over.status_code == 400 and "не больше 8" in over.json()["detail"]


def test_view_errors_leave_note_untouched(client, note, monkeypatch):
    from memory_desk import llm
    monkeypatch.setattr(llm, "ollama_json", lambda prompt, **k: (_ for _ in ()).throw(RuntimeError("Ollama не отвечает. Запустите Ollama.")))
    before = note.path.read_text(encoding="utf-8")
    assert client.post(f"/api/meetings/{note.id}/views", json={"presetId": "risks"}).status_code == 200
    state = wait_view(client, note.id)
    assert state["state"] == "error" and "Ollama" in state["message"]
    assert note.path.read_text(encoding="utf-8") == before
    assert client.get("/api/meetings/нет/views/status").json() == {"state": "idle"}


def test_long_transcript_is_read_in_parts_and_condensed(client, note, monkeypatch):
    long_text = "\n\n".join(f"[00:{i // 60:02d}:{i % 60:02d}] **Спикер 1:** " + "Обсуждаем детали проекта. " * 20 for i in range(1, 40))
    monkeypatch.setattr(knowledge, "meeting_transcript", lambda m: long_text)
    calls = []

    def answer(prompt):
        calls.append(prompt)
        if prompt.startswith("Ниже пункты"):
            return {"items": [{"text": "Итоговый пункт", "evidence_time": "00:00:30"}]}
        return {"items": [{"text": f"Пункт {len(calls)}-{i}", "evidence_time": "00:00:30"} for i in range(5)] + [{"text": "Повтор", "evidence_time": "00:00:30"}]}
    fake_views(monkeypatch, answer=answer)
    add_view(client, note, presetId="exec5")
    assert len(calls) >= 3 and calls[-1].startswith("Ниже пункты")
    view = client.get(f"/api/meetings/{note.id}").json()["views"][0]
    assert [i["text"] for i in view["items"]] == ["Итоговый пункт"]
    # дубли между частями не копятся, если сводить не нужно
    monkeypatch.setattr(views, "build_items", views.build_items)
    items = views.build_items(long_text, "Тест", "Выпиши всё что нужно", 100)
    assert len([i for i in items if i["text"] == "Повтор"]) == 1


def test_empty_answer_gives_explicit_placeholder(client, note, monkeypatch):
    fake_views(monkeypatch, answer=lambda p: {"items": []})
    add_view(client, note, presetId="client_questions")
    text = note.path.read_text(encoding="utf-8")
    assert "Не выявлено." in text.split("## Взгляд: Вопросы к заказчику")[1].split("## ")[0]
    assert client.get(f"/api/meetings/{note.id}").json()["views"][0]["items"] == []


def test_template_rebuild_keeps_views(client, note, monkeypatch):
    fake_model(monkeypatch)
    fake_views(monkeypatch)
    add_view(client, note, presetId="risks")
    view_text = views.sections_text(note.path.read_text(encoding="utf-8"))
    assert client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "lecture"}).status_code == 200
    assert wait_rebuild(client, note.id)["state"] == "done"
    after = note.path.read_text(encoding="utf-8")
    assert view_text in after and after.index("## Взгляд: Риски") < after.index("## Стенограмма")
    assert [v["id"] for v in client.get(f"/api/meetings/{note.id}").json()["views"]] == ["risks"]


def test_second_view_while_first_runs_is_refused(client, note, monkeypatch):
    import threading
    gate = threading.Event()

    def slow(prompt, **k):
        gate.wait(5)
        return {"items": []}
    from memory_desk import llm
    monkeypatch.setattr(llm, "ollama_json", slow)
    try:
        assert client.post(f"/api/meetings/{note.id}/views", json={"presetId": "risks"}).status_code == 200
        busy = client.post(f"/api/meetings/{note.id}/views", json={"presetId": "decisions"})
        assert busy.status_code == 409 and "уже строится" in busy.json()["detail"]
        assert client.get(f"/api/meetings/{note.id}/views/status").json()["state"] == "running"
    finally:
        gate.set()
        wait_view(client, note.id)


# --------------------------------------------------------------------------- 3.3 сохранить ответ чата в Obsidian

@pytest.fixture()
def answers_dir(client):
    import shutil
    from memory_desk import knowledge as k
    folder = k.WORKSPACE_DIR / "Ответы"
    shutil.rmtree(folder, ignore_errors=True)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def chat_payload(note):
    return {
        "question": "Что решили про справочник?", "answer": "Решили вести справочник клиентов в КХД.\n# Не заголовок\n- Иван готовит выгрузку",
        "scope": "Проект: КХД",
        "sources": [{"meetingId": note.id, "title": "левое название", "date": "2026-10-10", "time": "11:00", "t": "00:02:00",
                     "tSec": 120, "excerpt": "Решили: справочник ведём в КХД."},
                    {"meetingId": "нет-такой", "title": "Удалённая запись | [x]", "t": "", "excerpt": "Фрагмент"}]}


def test_save_answer_writes_note_with_links_and_timecodes(client, note, answers_dir):
    response = client.post("/api/chat/save", json=chat_payload(note))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["already"] is False and "Ответы/" in data["savedAs"]
    assert data["obsidianUrl"].startswith("obsidian://open")
    files = list(answers_dir.glob("*.md"))
    assert len(files) == 1 and files[0].name.startswith(f"{datetime.now():%Y-%m-%d} — Что решили про справочник")
    text = files[0].read_text(encoding="utf-8")
    assert "type: chat_answer" in text and 'question: "Что решили про справочник?"' in text and 'scope: "Проект: КХД"' in text
    assert re.search(r"(?m)^# Что решили про справочник\?$", text) and "Решили вести справочник клиентов в КХД." in text
    assert "\\# Не заголовок" in text                                  # заголовок из ответа не ломает структуру
    rel = knowledge.note_relative_path(note.path).removesuffix(".md")
    assert f"[[{rel}|Разговор о справочнике]]" in text                  # название и путь — из архива, не из присланного
    assert "— 10.10.2026, 11:00 · `⏱ 00:02:00`" in text and "> «Решили: справочник ведём в КХД.»" in text
    assert "Удалённая запись / (x)" in text and "[[нет-такой" not in text   # неизвестная запись — просто текст
    assert not any(m["title"].startswith("Что решили") for m in client.get("/api/meetings", params={"limit": 200}).json()["items"])


def test_save_answer_twice_makes_no_duplicates_and_keeps_user_edits(client, note, answers_dir):
    first = client.post("/api/chat/save", json=chat_payload(note)).json()
    path = next(answers_dir.glob("*.md"))
    path.write_text(path.read_text(encoding="utf-8") + "\nМоя пометка в Obsidian\n", encoding="utf-8")
    second = client.post("/api/chat/save", json=chat_payload(note)).json()
    assert second["already"] is True and second["savedAs"] == first["savedAs"]
    assert len(list(answers_dir.glob("*.md"))) == 1 and "Моя пометка в Obsidian" in path.read_text(encoding="utf-8")
    # другой ответ на тот же вопрос — отдельная заметка
    other = chat_payload(note) | {"answer": "Другой ответ."}
    assert client.post("/api/chat/save", json=other).json()["already"] is False
    assert len(list(answers_dir.glob("*.md"))) == 2


def test_save_answer_same_next_day_finds_existing(client, note, answers_dir):
    from memory_desk import answers
    payload = chat_payload(note)
    yesterday = datetime.now() - timedelta(days=1)
    first = answers.save(client.app.state.core, payload["question"], payload["answer"], payload["sources"], when=yesterday)
    assert first["already"] is False
    again = client.post("/api/chat/save", json=payload).json()
    assert again["already"] is True and again["savedAs"] == first["savedAs"] and len(list(answers_dir.glob("*.md"))) == 1


def test_save_answer_validation(client, note, answers_dir):
    assert client.post("/api/chat/save", json={"question": " ", "answer": "x"}).status_code == 400
    assert client.post("/api/chat/save", json={"question": "Вопрос", "answer": "  "}).status_code == 400
    assert client.post("/api/chat/save", json={"question": "Вопрос"}).status_code == 422
    weird = chat_payload(note) | {"question": 'Что/где: "когда"? <это> | всё*'}
    assert client.post("/api/chat/save", json=weird).status_code == 200
    name = next(answers_dir.glob("*.md")).name
    assert not re.search(r'[\\/:*?"<>|]', name)
