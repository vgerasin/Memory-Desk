"""Этап 4.1: экспорт документов в Markdown, HTML, PDF и DOCX."""
from __future__ import annotations

import io
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import memory_desk.export  # noqa: F401
    globals()["export"] = memory_desk.export


def protocol_markdown(rows: int = 60) -> str:
    table = "\n".join(
        f"| {i} | Подготовить выгрузку по справочнику клиентов и согласовать формат, пункт {i} — `⏱ 00:{i % 60:02d}:10` | Иван Петров | 2026-11-{i % 28 + 1:02d} | в работе |"
        for i in range(1, rows + 1))
    return f"""# Протокол: Созвон по архитектуре хранилища

**Дата:** 10 октября 2026, 11:30 · **Длительность:** 00:45:10
**Участники:** Иван Петров, Анна Смирнова

## Повестка и темы

- Единый справочник клиентов в КХД
- Инкремент по _дате изменения_ для **витрин** продаж

## Решения

1. Справочник ведём в КХД
2. Инкремент по дате изменения

## Другой список

1. Первый
2. Второй

## Поручения

| № | Поручение | Ответственный | Срок | Статус |
| --- | --- | --- | --- | --- |
{table}

> [!info] Проверка
> Составлено автоматически; проверьте перед рассылкой.

```mermaid
mindmap
  root((Проект))
```

---

_Составлено автоматически из записи «Созвон»._
"""


def pdf_text(data: bytes) -> tuple[str, int]:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() for page in reader.pages), len(reader.pages)


# --------------------------------------------------------------------------- разбор

def test_parser_blocks():
    blocks = export.parse(protocol_markdown(3))
    kinds = [b.kind for b in blocks]
    assert kinds[0] == "heading" and blocks[0].level == 1 and export.plain(blocks[0].runs) == "Протокол: Созвон по архитектуре хранилища"
    assert kinds.count("ol") == 2 and kinds.count("table") == 1 and "quote" in kinds and "code" in kinds and "hr" in kinds
    table = next(b for b in blocks if b.kind == "table")
    assert [export.plain(c) for c in table.header] == ["№", "Поручение", "Ответственный", "Срок", "Статус"]
    assert len(table.rows) == 3 and all(len(r) == 5 for r in table.rows)
    callout = next(b for b in blocks if b.kind == "quote")
    assert callout.title == "Проверка" and export.plain(callout.lines[0]).startswith("Составлено автоматически")
    meta = blocks[1]
    assert meta.kind == "para" and any(r.text == "\n" for r in meta.runs)    # строки «Дата» и «Участники» не склеиваются


def test_inline_markup_and_obsidian_syntax():
    runs = export.inline("**жирный _и курсив_**, `код`, [[Транскрипты/Запись.md|Запись]], [сайт](https://example.com) и [[Без псевдонима]]")
    flags = {r.text: (r.bold, r.italic, r.code, r.href) for r in runs}
    assert flags["жирный "] == (True, False, False, "") and flags["и курсив"] == (True, True, False, "")
    assert flags["код"][2] is True and "Запись" in flags and flags["сайт"][3] == "https://example.com" and "Без псевдонима" in flags
    assert not any("[[" in r.text for r in runs)
    assert export.inline("[x](javascript:alert(1))")[0].href == ""        # опасные ссылки не становятся ссылками


def test_frontmatter_is_dropped():
    blocks = export.parse("---\ntype: chat_answer\nquestion: \"x\"\n---\n\n# Вопрос\n\nОтвет.\n")
    assert [b.kind for b in blocks] == ["heading", "para"]


# --------------------------------------------------------------------------- форматы

def test_markdown_is_unchanged():
    text = protocol_markdown(3)
    data, name, mime = export.export("md", "", text)
    assert data.decode("utf-8") == text and name == "Протокол — Созвон по архитектуре хранилища.md" and mime.startswith("text/markdown")


def test_html_is_self_contained_and_complete():
    data, name, mime = export.export("html", "", protocol_markdown(60), when=datetime(2026, 10, 13))
    page = data.decode("utf-8")
    assert mime.startswith("text/html") and name.endswith(".html") and page.startswith("<!DOCTYPE html>")
    assert not re.search(r'(?i)(src|href)\s*=\s*"(https?:)?//', page) and "url(" not in page and "@import" not in page   # открывается без интернета
    assert not re.search(r"(?i)<script|<link ", page)
    assert page.count("<tr>") == 61 and "<th>Поручение</th>" in page and "пункт 60" in page          # все строки таблицы на месте
    assert "<ol><li>Справочник ведём в КХД</li>" in page and page.count("<ol>") == 2
    assert "<em>дате изменения</em>" in page and "<strong>витрин</strong>" in page and "<br>" in page
    assert "Memory Desk · 13.10.2026" in page and "&lt;" not in page.split("<style>")[0]
    assert "Проверка" in page and "<pre><code>mindmap" in page


def test_html_escapes_markup_in_text():
    data, _, _ = export.export("html", "T", "# Заголовок\n\n<script>alert(1)</script> & **<b>x</b>**\n")
    page = data.decode("utf-8")
    assert "<script>" not in page.replace("<style>", "") and "&lt;script&gt;" in page


def test_pdf_five_page_protocol_has_cyrillic_and_all_rows():
    data, name, mime = export.export("pdf", "", protocol_markdown(120))
    assert mime == "application/pdf" and name.endswith(".pdf") and data.startswith(b"%PDF")
    text, pages = pdf_text(data)
    assert pages >= 5
    assert "Протокол: Созвон по архитектуре хранилища" in text.replace("\n", " ") and "Ответственный" in text   # слово шапки не разорвано
    flat = " ".join(text.split())
    for number in (1, 60, 120):   # ни одна строка таблицы не потеряна
        assert f"пункт {number} " in flat or f"пункт {number}" in flat
    assert flat.count("Ответственный") >= pages - 1   # шапка таблицы повторяется на следующих страницах
    assert "Memory Desk · стр. 1" in flat and f"стр. {pages}" in flat
    assert "Другой список" in flat and "1. Первый" in flat.replace("\n", " ")


def test_pdf_outline_and_metadata():
    pypdf = pytest.importorskip("pypdf")
    data, _, _ = export.export("pdf", "Мой документ", protocol_markdown(5))
    reader = pypdf.PdfReader(io.BytesIO(data))
    assert reader.metadata.title == "Мой документ"
    def flat(items):
        for item in items:
            yield from flat(item) if isinstance(item, list) else [item.title]
    titles = list(flat(reader.outline))
    assert "Решения" in titles and "Поручения" in titles and titles[0].startswith("Протокол")


def test_docx_uses_word_styles_and_real_tables():
    docx = pytest.importorskip("docx")
    data, name, mime = export.export("docx", "", protocol_markdown(60))
    assert name.endswith(".docx") and "wordprocessingml" in mime
    assert zipfile.ZipFile(io.BytesIO(data)).testzip() is None
    doc = docx.Document(io.BytesIO(data))
    styles = [p.style.name for p in doc.paragraphs]
    assert styles.count("Heading 1") == 1 and styles.count("Heading 2") == 4          # заголовки Word, а не крупный шрифт
    assert styles.count("List Bullet") == 2 and styles.count("List Number") == 4
    assert len(doc.tables) == 1 and len(doc.tables[0].rows) == 61 and doc.tables[0].rows[60].cells[1].text.startswith("Подготовить")
    assert doc.core_properties.title == "Протокол: Созвон по архитектуре хранилища"
    assert any("Проверка" in p.text for p in doc.paragraphs)
    # у каждого нумерованного списка своя нумерация с единицы
    num_ids = {p._p.pPr.numPr.numId.val for p in doc.paragraphs if p.style.name == "List Number"}
    assert len(num_ids) == 2
    xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
    assert "w:tblHeader" in xml and "w:fldChar" in zipfile.ZipFile(io.BytesIO(data)).read("word/footer1.xml").decode("utf-8")


def test_empty_and_unknown_are_refused():
    with pytest.raises(export.ExportError):
        export.export("pdf", "x", "   ")
    with pytest.raises(export.ExportError):
        export.export("rtf", "x", "# a")
    with pytest.raises(export.ExportError):
        export.export("md", "x", "a" * (export.MAX_CHARS + 1))


def test_filename_is_safe():
    assert not re.search(r'[\\/:*?"<>|]', export.safe_filename('Протокол: Что/где? "когда" <это> | всё*'))
    assert len(export.safe_filename("Очень длинное название " * 20)) <= 90
    header = export.content_disposition("Протокол — тест.pdf")
    assert "filename*=UTF-8''" in header and header.isascii()


def test_document_with_only_table_or_code_and_odd_input():
    for fmt in ("html", "pdf", "docx"):
        data, _, _ = export.export(fmt, "Только таблица", "| a | b |\n| - | - |\n| 1 | 2 |\n\n```\nкод   с   пробелами\n```\n\n| x |\n| - |\n")
        assert len(data) > 500
    data, _, _ = export.export("pdf", "", "Просто текст без заголовка, " + "очень длинное_слово_без_пробелов" * 12)
    assert data.startswith(b"%PDF")


# --------------------------------------------------------------------------- API

def test_export_api(client):
    body = {"format": "pdf", "title": "Протокол", "markdown": protocol_markdown(10)}
    response = client.post("/api/export", json=body)
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''" in response.headers["content-disposition"] and response.content.startswith(b"%PDF")
    for fmt, marker in (("docx", b"PK"), ("html", b"<!DOCTYPE html>"), ("md", b"# ")):
        r = client.post("/api/export", json={**body, "format": fmt, "name": "Мой протокол"})
        assert r.status_code == 200 and r.content.startswith(marker) and "%D0%9C%D0%BE%D0%B9" in r.headers["content-disposition"]
    assert client.post("/api/export", json={**body, "format": "exe"}).status_code == 400
    assert client.post("/api/export", json={**body, "markdown": " "}).status_code == 400
    assert client.post("/api/export", json={"format": "pdf"}).status_code == 422


def test_chat_answer_export(client):
    payload = {"question": "Что решили про справочник?", "answer": "Решили вести справочник клиентов в КХД.\n- Иван готовит выгрузку",
               "scope": "Весь архив", "sources": [{"meetingId": "нет", "title": "Созвон", "date": "2026-10-10", "time": "11:00", "t": "00:02:00",
                                                    "excerpt": "Решили: справочник ведём в КХД."}]}
    for fmt in ("pdf", "docx", "html", "md"):
        r = client.post("/api/chat/export", json={**payload, "format": fmt})
        assert r.status_code == 200, (fmt, r.text)
        assert "%D0%A7%D1%82%D0%BE" in r.headers["content-disposition"]
    html = client.post("/api/chat/export", json={**payload, "format": "html"}).text
    assert "<h1>Что решили про справочник?</h1>" in html and "Источники" in html and "00:02:00" in html and "type: chat_answer" not in html
    text, _ = pdf_text(client.post("/api/chat/export", json={**payload, "format": "pdf"}).content)
    assert "Что решили про справочник?" in text.replace("\n", " ") and "Источники" in text
