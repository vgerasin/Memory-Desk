"""Этап 4.2: карта как PNG (SVG и OPML убраны в этапе 14)."""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _modules(fixture_dir):
    sys.path.insert(0, str(ROOT))
    import memory_desk.export  # noqa: F401
    import memory_desk.mapexport  # noqa: F401
    globals()["mapexport"] = memory_desk.mapexport
    globals()["export"] = memory_desk.export


def leaf(text):
    return {"text": text, "children": []}


def big_tree(branches=4, per=10):
    """Карта на 45 узлов: корень, 4 ветки, по 10 пунктов, часть с подпунктами."""
    kids = []
    for b in range(branches):
        items = [{"text": f"Пункт {b}.{i}: подготовить выгрузку по справочнику клиентов и согласовать формат с заказчиком",
                  "children": [leaf("подпункт")] if i == 0 else []} for i in range(per)]
        kids.append({"text": ["Решения", "Задачи", "Риски", "Вопросы"][b % 4], "children": items})
    return {"text": "Созвон по архитектуре хранилища", "children": kids}


def count(tree):
    return 1 + sum(count(c) for c in tree["children"])


def test_png_is_wide_and_readable():
    tree = big_tree()
    assert count(tree) >= 40
    data = mapexport.render_png(mapexport.parse_tree(tree))
    image = Image.open(io.BytesIO(data))
    assert image.format == "PNG"
    assert image.width >= 2000
    assert image.height >= 1500
    # на картинке есть и фон, и цветные ветки, и тёмный корень
    colors = {c for _, c in image.convert("RGB").getcolors(maxcolors=1_000_000)}
    assert (255, 255, 255) in colors and (0x1d, 0x1d, 0x1f) in colors and (0x2f, 0x6f, 0xed) in colors


def test_small_map_is_still_2000_wide():
    data = mapexport.render_png(mapexport.parse_tree({"text": "Мини", "children": [leaf("раз"), leaf("два")]}))
    assert Image.open(io.BytesIO(data)).width >= 2000


def test_layout_has_no_overlaps_and_parent_is_centered():
    lay = mapexport.layout(mapexport.parse_tree(big_tree()))
    boxes = lay.boxes
    for i, a in enumerate(boxes):
        assert a.x >= 0 and a.y >= 0 and a.x + a.w <= lay.width and a.y + a.h <= lay.height
        for b in boxes[i + 1:]:
            apart = a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y
            assert apart, (a.text, b.text)
    for b in boxes:
        if b.children:
            mid = (b.children[0].cy + b.children[-1].cy) / 2
            assert abs(b.cy - mid) < 1
            assert all(c.x > b.x + b.w for c in b.children)


def test_tree_validation():
    with pytest.raises(export.ExportError):
        mapexport.parse_tree("строка")
    with pytest.raises(export.ExportError):
        mapexport.parse_tree({"text": "x", "children": "нет"})
    deep = {"text": "0", "children": []}
    node = deep
    for i in range(12):
        node["children"] = [{"text": str(i), "children": []}]
        node = node["children"][0]
    with pytest.raises(export.ExportError):
        mapexport.parse_tree(deep)
    with pytest.raises(export.ExportError):
        mapexport.parse_tree({"text": "x", "children": [leaf("a")] * 700})
    # пустой и очень длинный текст
    clean = mapexport.parse_tree({"text": "", "children": [leaf("я" * 1000), {"text": None}]})
    assert clean["text"] == "—" and len(clean["children"][0]["text"]) <= mapexport.MAX_TEXT
    assert clean["children"][1] == {"text": "—", "children": []}


def test_export_names_and_unknown_format():
    data, name, mime = mapexport.export("png", "Альфа: итоги", {"text": "Альфа", "children": []})
    assert name == "Карта — Альфа — итоги.png" and mime == "image/png" and data.startswith(b"\x89PNG")
    data, name, mime = mapexport.export("png", "", {"text": "Альфа", "children": []}, name="Моя карта.png")
    assert name == "Моя карта.png"
    for gone in ("svg", "opml", "pdf"):
        with pytest.raises(export.ExportError):
            mapexport.export(gone, "x", {"text": "x", "children": []})


def test_map_export_api(client):
    body = {"title": "Карта проекта", "tree": big_tree(2, 4)}
    r = client.post("/api/export/map", json={**body, "format": "png"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/png") and r.content.startswith(b"\x89PNG")
    assert "filename*=UTF-8''" in r.headers["content-disposition"] and ".png" in r.headers["content-disposition"]
    for gone in ("svg", "opml", "gif"):
        assert client.post("/api/export/map", json={**body, "format": gone}).status_code == 400
    assert client.post("/api/export/map", json={"format": "png", "tree": {"text": "x", "children": "нет"}}).status_code == 400
    assert client.post("/api/export/map", json={"format": "png"}).status_code == 422


def test_map_from_real_scope_exports(client):
    """Карта, построенная сервером по записи, выгружается без правок."""
    items = client.get("/api/meetings?limit=5").json()["items"]
    if not items:
        pytest.skip("в тестовых данных нет записей")
    m = client.post("/api/materials/mindmap", json={"meetingId": items[0]["id"]})
    if m.status_code != 200:
        pytest.skip(m.text)
    result = m.json()
    r = client.post("/api/export/map", json={"format": "png", "title": result["title"], "tree": result["tree"]})
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
