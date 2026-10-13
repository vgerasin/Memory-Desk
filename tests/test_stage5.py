"""Этап 5: вложения к записи (фото и текст), распознавание текста, поиск по вложениям."""
from __future__ import annotations

import io
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

OCR_TEXT = "Доска после планёрки\nПлан запуска\n# Не раздел\n  ## и это тоже\nСрок — 15 ноября"


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import meetings_process
    import memory_desk.attachments, memory_desk.knowledge, memory_desk.ocr, memory_desk.rebuild  # noqa: F401
    import test_stage3 as s3
    s3.mp, s3.knowledge = meetings_process, memory_desk.knowledge
    globals().update(attachments=memory_desk.attachments, knowledge=memory_desk.knowledge, ocr=memory_desk.ocr,
                     rebuild=memory_desk.rebuild, s3=s3)


@pytest.fixture()
def note(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DESK_DATA_DIR", str(tmp_path))
    from memory_desk import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    created = s3.Note(client).create()
    yield created
    created.remove()
    rebuild.STATE.clear()


@pytest.fixture()
def fake_ocr(monkeypatch):
    calls = []

    def backend(path):
        calls.append(path)
        return ocr.Result(OCR_TEXT, "Vision")
    monkeypatch.setattr(ocr, "BACKEND", backend)
    return calls


def png(color=(250, 250, 250), size=(60, 40)) -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def upload(client, note, name, data):
    return client.post(f"/api/meetings/{note.id}/attachments", params={"name": name}, content=data,
                       headers={"content-type": "application/octet-stream"})


def folder_of(note) -> Path:
    return note.path.parent / "Вложения" / note.path.stem


# --------------------------------------------------------------------------- добавление и разбор

def test_photo_is_saved_recognized_and_written_into_note(client, note, fake_ocr):
    before = note.path.read_text(encoding="utf-8")
    r = upload(client, note, "доска.png", png())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "image" and body["draft"] is True and body["engine"] == "Vision" and not body["already"]
    assert body["text"] == OCR_TEXT and body["exists"] and body["url"].endswith(f"/attachments/{body['id']}/file")
    files = list(folder_of(note).iterdir())
    assert len(files) == 1 and files[0].name == f"{body['id']}-доска.png"
    text = note.path.read_text(encoding="utf-8")
    assert text.count("\n## Вложения\n") == 1
    assert f"![[{body['path']}|480]]" in text and "> фото · Vision · черновик" in text
    assert "\n\\# Не раздел" in text and "\n\\  ## и это тоже" in text and "\n# Не раздел" not in text
    assert text.index("## Вложения") < text.index("Стенограмма")            # раздел встал перед стенограммой
    assert "## Вложения" not in knowledge.parse_note_sections(before)
    # текст читается обратно без экранирования, а чужое содержимое заметки не тронуто
    detail = client.get(f"/api/meetings/{note.id}").json()
    assert [a["text"] for a in detail["attachments"]] == [OCR_TEXT]
    assert "Вложения" not in [s["title"] for s in detail["extraSections"]]
    assert before.rstrip() in text.replace(attachments.section_text(text) + "\n", "")   # остальное содержимое на месте
    assert len(fake_ocr) == 1


def test_remove_restores_note_byte_for_byte_and_deletes_files(client, note, fake_ocr):
    before = note.path.read_bytes()
    att = upload(client, note, "доска.png", png()).json()
    assert client.delete(f"/api/meetings/{note.id}/attachments/{att['id']}").json() == {"ok": True}
    assert note.path.read_bytes() == before
    assert not (note.path.parent / "Вложения").exists()
    assert client.delete(f"/api/meetings/{note.id}/attachments/{att['id']}").status_code == 404


def test_same_file_is_not_added_twice(client, note, fake_ocr):
    first = upload(client, note, "а.png", png()).json()
    again = upload(client, note, "б.png", png()).json()
    assert again["already"] is True and again["id"] == first["id"]
    assert note.path.read_text(encoding="utf-8").count("![[") == 1 and len(fake_ocr) == 1


def test_validation(client, note, fake_ocr, monkeypatch):
    assert upload(client, note, "пусто.png", b"").status_code == 400
    assert upload(client, note, "архив.zip", b"PK\x03\x04").status_code == 400
    assert upload(client, note, "сломан.png", "это не картинка".encode()).status_code == 400
    assert upload(client, note, "", png()).status_code == 400
    assert client.post("/api/meetings/нет/attachments", params={"name": "a.png"}, content=png()).status_code == 404
    monkeypatch.setattr(attachments, "MAX_BYTES", 100)
    assert upload(client, note, "большой.png", png(size=(400, 400))).status_code in (400, 413)
    monkeypatch.setattr(attachments, "MAX_BYTES", 25 * 1024 * 1024)
    monkeypatch.setattr(attachments, "MAX_PER_RECORD", 1)
    assert upload(client, note, "первый.png", png((1, 2, 3))).status_code == 200
    assert upload(client, note, "второй.png", png((9, 8, 7))).status_code == 400
    assert not (note.path.parent / "Вложения" / note.path.stem / "второй.png").exists()
    assert len(list(folder_of(note).iterdir())) == 1       # неудачная загрузка не оставила файла
    assert client.post(f"/api/meetings/{note.id}/attachments", params={"name": "x.png"}, content=png(),
                       headers={"x-memory-desk": ""}).status_code == 403


def test_text_file_and_pasted_text(client, note):
    sheet = "Список вопросов\n# Первый\nВторой вопрос".encode("cp1251")
    r = upload(client, note, "вопросы.md", sheet)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["kind"] == "text" and first["draft"] is False and first["text"].startswith("Список вопросов")
    assert next(folder_of(note).iterdir()).suffix == ".txt"           # .md в хранилище не кладём: его прочёл бы как запись
    pasted = client.post(f"/api/meetings/{note.id}/attachments/text", json={"name": "Мысли", "text": "Позвонить заказчику\n\nПроверить выгрузку"})
    assert pasted.status_code == 200 and pasted.json()["name"] == "Мысли"
    assert client.post(f"/api/meetings/{note.id}/attachments/text", json={"name": "x", "text": "  \n "}).status_code == 400
    items = client.get(f"/api/meetings/{note.id}/attachments").json()["items"]
    assert [i["name"] for i in items] == ["вопросы", "Мысли"]
    assert "> текст · файл · проверено" in note.path.read_text(encoding="utf-8")
    titles = [m["title"] for m in client.get("/api/meetings", params={"limit": 1000}).json()["items"]]
    assert "вопросы" not in titles and "Мысли" not in titles          # вложения не становятся записями


def test_hand_added_lines_in_section_survive_edits(client, note, fake_ocr):
    att = upload(client, note, "доска.png", png()).json()
    text = note.path.read_text(encoding="utf-8")
    note.path.write_text(text.replace("## Вложения\n\n", "## Вложения\n\nМоя приписка от руки\n\n", 1), encoding="utf-8")
    client.app.state.core.reload(force=True)
    assert client.patch(f"/api/meetings/{note.id}/attachments/{att['id']}", json={"text": "Поправил"}).status_code == 200
    after = note.path.read_text(encoding="utf-8")
    assert "Моя приписка от руки" in after and "Поправил" in after
    client.delete(f"/api/meetings/{note.id}/attachments/{att['id']}")
    assert "Моя приписка от руки" in note.path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- правка и повторное распознавание

def test_editing_text_marks_it_checked_and_rename(client, note, fake_ocr):
    att = upload(client, note, "доска.png", png()).json()
    r = client.patch(f"/api/meetings/{note.id}/attachments/{att['id']}", json={"text": "Исправленный текст\n- пункт", "name": "Доска 12.10"})
    assert r.status_code == 200 and r.json()["draft"] is False and r.json()["name"] == "Доска 12.10"
    text = note.path.read_text(encoding="utf-8")
    assert "### Доска 12.10" in text and "> фото · Vision · проверено" in text and "Исправленный текст" in text
    assert client.patch(f"/api/meetings/{note.id}/attachments/нет", json={"text": "x"}).status_code == 404
    # повторное распознавание не стирает правку без подтверждения
    assert client.post(f"/api/meetings/{note.id}/attachments/{att['id']}/recognize").status_code == 409
    again = client.post(f"/api/meetings/{note.id}/attachments/{att['id']}/recognize", params={"force": "true"})
    assert again.status_code == 200 and again.json()["text"] == OCR_TEXT and again.json()["draft"] is True


def test_recognize_again_without_engine_and_for_text(client, note, monkeypatch):
    def broken(_path):
        raise ocr.OcrError("Нет движка распознавания")
    monkeypatch.setattr(ocr, "BACKEND", broken)
    r = upload(client, note, "доска.png", png())
    assert r.status_code == 200, r.text                                       # снимок сохраняется и без текста
    att = r.json()
    assert att["text"] == "" and att["draft"] is True and "Нет движка" in att["warning"]
    assert client.post(f"/api/meetings/{note.id}/attachments/{att['id']}/recognize").status_code == 422
    ok = client.post(f"/api/meetings/{note.id}/attachments/text", json={"name": "Т", "text": "текст"}).json()
    assert client.post(f"/api/meetings/{note.id}/attachments/{ok['id']}/recognize").status_code == 400


# --------------------------------------------------------------------------- файл

def test_file_is_served_only_from_the_attachment_folder(client, note, fake_ocr):
    data = png((10, 20, 30))
    att = upload(client, note, "доска.png", data).json()
    got = client.get(att["url"])
    assert got.status_code == 200 and got.content == data and got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    # путь в заметке указывают на чужой файл — он не отдаётся
    secret = note.path.parent / "секрет.txt"
    secret.write_text("секрет", encoding="utf-8")
    text = note.path.read_text(encoding="utf-8").replace(att["path"], str(secret.relative_to(knowledge.OBSIDIAN_VAULT_ROOT)))
    note.path.write_text(text, encoding="utf-8")
    client.app.state.core.reload(force=True)
    listed = client.get(f"/api/meetings/{note.id}/attachments").json()["items"][0]
    assert listed["exists"] is False and listed["url"] is None
    assert client.get(att["url"]).status_code == 404
    traversal = text.replace(str(secret.relative_to(knowledge.OBSIDIAN_VAULT_ROOT)), "../" * 6 + "etc/hostname")
    note.path.write_text(traversal, encoding="utf-8")
    client.app.state.core.reload(force=True)
    assert client.get(att["url"]).status_code == 404


# --------------------------------------------------------------------------- поиск, протокол, пересборка

def test_attachment_text_is_searchable_and_in_protocol(client, note, fake_ocr, monkeypatch):
    upload(client, note, "доска.png", png())
    client.post(f"/api/meetings/{note.id}/attachments/text", json={"name": "Мысли", "text": "Позвонить заказчику насчёт квартального отчёта"})
    meeting = client.app.state.core.by_id[note.id]
    chunks = [c for c in knowledge.sections(meeting) if c["section"] == "Вложения"]
    texts = [c["text"] for c in chunks]
    assert any(t.startswith("Фото «доска.png» (черновик распознавания): ") and "15 ноября" in t for t in texts)
    assert any(t.startswith("Текст «Мысли»: ") and "квартального отчёта" in t for t in texts)
    assert not any("![[" in t for t in texts)
    from memory_desk import digest
    monkeypatch.setattr(digest, "generate", lambda *a, **k: "")
    protocol = client.post(f"/api/meetings/{note.id}/protocol").json()["markdown"]
    assert "## Материалы к записи" in protocol and "**доска.png** (фото, черновик распознавания)" in protocol
    assert "**Мысли** (текст)" in protocol


def test_rebuild_keeps_attachments_and_gives_model_their_text(client, note, fake_ocr, monkeypatch):
    calls = []
    s3.fake_model(monkeypatch, calls=calls)
    att = upload(client, note, "доска.png", png()).json()
    assert client.post(f"/api/meetings/{note.id}/rebuild", json={"templateId": "lecture"}).status_code == 200
    state = s3.wait_rebuild(client, note.id)
    assert state["state"] == "done", state
    after = note.path.read_text(encoding="utf-8")
    assert after.count("## Вложения") == 1 and f"![[{att['path']}|480]]" in after and "15 ноября" in after
    assert any("Материалы, приложенные к записи" in c and "Доска после планёрки" in c for c in calls)
    assert client.get(f"/api/meetings/{note.id}").json()["attachments"][0]["id"] == att["id"]


def test_no_attachments_means_no_context_for_model(client, note):
    assert attachments.model_context(client.app.state.core.by_id[note.id]) == ""


def test_erase_files_helper_removes_folder(client, note, fake_ocr):
    upload(client, note, "доска.png", png())
    meeting = client.app.state.core.by_id[note.id]
    assert attachments.erase_files(meeting) == 1
    assert not (note.path.parent / "Вложения").exists() and attachments.erase_files(meeting) == 0


# --------------------------------------------------------------------------- движки распознавания

def test_system_status_lists_ocr_row(client, monkeypatch):
    monkeypatch.setattr(ocr, "BACKEND", None)
    monkeypatch.setattr(ocr, "_vision_available", lambda: False)
    monkeypatch.setattr(ocr, "tesseract_path", lambda: None)
    row = next(r for r in client.get("/api/system").json()["rows"] if r["key"] == "ocr")
    assert row["state"] == "warn" and "нет движка" in row["value"]
    monkeypatch.setattr(ocr, "_vision_available", lambda: True)
    row = next(r for r in client.get("/api/system").json()["rows"] if r["key"] == "ocr")
    assert row["state"] == "ok" and "Vision" in row["value"]


def test_recognize_without_any_engine_explains_what_to_install(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "BACKEND", None)
    monkeypatch.setattr(ocr, "_vision_available", lambda: False)
    monkeypatch.setattr(ocr, "tesseract_path", lambda: None)
    with pytest.raises(ocr.OcrError, match="tesseract"):
        ocr.recognize(tmp_path / "x.png")


def test_vision_failure_falls_back_to_tesseract(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "BACKEND", None)
    monkeypatch.setattr(ocr, "_vision_available", lambda: True)
    monkeypatch.setattr(ocr, "tesseract_path", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr, "vision", lambda _p: (_ for _ in ()).throw(RuntimeError("сбой")))
    monkeypatch.setattr(ocr, "tesseract", lambda _p: ocr.Result("текст", "Tesseract"))
    assert ocr.recognize(tmp_path / "x.png").engine == "Tesseract"


def test_clean_drops_noise_and_blank_lines():
    assert ocr.clean("  Привет   мир \n\n.\nа\n   \n- пункт 1  \r\n") == "Привет мир\nа\n- пункт 1"


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract не установлен")
def test_real_tesseract_reads_printed_english(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for candidate in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", str(ROOT / "memory_desk/fonts/DejaVuSans.ttf")):
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, 48)
            break
    if font is None:
        pytest.skip("нет шрифта")
    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 20), "Project launch plan", fill="black", font=font)
    draw.text((30, 110), "Deadline: November", fill="black", font=font)
    path = tmp_path / "board.png"
    image.save(path)
    result = ocr.tesseract(path)
    assert result.engine == "Tesseract" and "launch" in result.text.lower() and "november" in result.text.lower()


def test_parser_roundtrip_and_unknown_blocks():
    body = ("Приписка\n\n### Фото.jpg\n![[Папка/Вложения/x/ab12cd34-фото.jpg|480]]\n> фото · Tesseract · черновик\nстрока 1\n\\# решётка\n\n"
            "### Чужой блок\nбез ссылки на файл\n\n### Заметка\n[[Папка/Вложения/x/ff00ff00-заметка.txt]]\n> текст · файл · проверено\nтекст")
    entries = attachments.parse_body(body)
    real = attachments.real(entries)
    assert [(e.id, e.kind, e.engine, e.draft) for e in real] == [("ab12cd34", "image", "Tesseract", True), ("ff00ff00", "text", "файл", False)]
    assert real[0].text == "строка 1\n# решётка"
    rendered = attachments.render(entries)
    assert "Приписка" in rendered and "### Чужой блок\nбез ссылки на файл" in rendered
    assert attachments.parse_body(rendered.partition("\n")[2]) == entries or \
        [e.text for e in attachments.real(attachments.parse_body(rendered.partition("\n")[2]))] == [e.text for e in real]
