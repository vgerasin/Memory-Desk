"""Выгрузка карты в PNG (этап 4.2; SVG и OPML убраны в этапе 14: PNG хватает, остальное было лишним).

Карта приходит деревом `{"text", "children"}`. Раскладка считается один раз (дерево слева направо,
родитель по центру своих детей), PNG рисуется через Pillow со шрифтом DejaVu из `memory_desk/fonts`.
Браузер, Mermaid и системные библиотеки не нужны.
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

from .export import FONT_DIR, ExportError, content_disposition, safe_filename

__all__ = ["FORMATS", "export", "content_disposition", "layout", "parse_tree", "render_png"]

FORMATS = {
    "png": ("image/png", "PNG"),
}

MAX_NODES = 600
MAX_DEPTH = 8
MAX_TEXT = 300
MIN_PNG_WIDTH = 2000
MAX_PNG_PIXELS = 40_000_000

PALETTE = ["#2f6fed", "#15803d", "#c2410c", "#7c3aed", "#be185d", "#0e7490", "#a16207", "#475569"]
INK = "#1d1d1f"
SURFACE = "#ffffff"
ROOT_FILL = "#1d1d1f"
FONT_FAMILY = "'DejaVu Sans', 'Helvetica Neue', Arial, sans-serif"

MARGIN = 40
COL_GAP = 72
ROW_GAP = 12
PAD_X = 14
PAD_Y = 9
MAX_LINES = 5
LEVELS = {  # глубина → (размер шрифта, жирный, ширина текста до переноса)
    0: (22, True, 260),
    1: (17, True, 240),
}
DEEP = (15, False, 300)


# --------------------------------------------------------------------------- дерево

def _clean(text: object) -> str:
    text = re.sub(r"\s+", " ", str(text if text is not None else "")).strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - 1].rstrip() + "…"
    return text or "—"


def parse_tree(raw: object) -> dict:
    """Проверяет и нормализует дерево из запроса: типы, глубина, число узлов, длина текста."""
    count = [0]

    def walk(node: object, depth: int) -> dict:
        if not isinstance(node, dict):
            raise ExportError("Карта повреждена: узел должен быть объектом")
        if depth > MAX_DEPTH:
            raise ExportError(f"Карта слишком глубокая: не больше {MAX_DEPTH} уровней")
        count[0] += 1
        if count[0] > MAX_NODES:
            raise ExportError(f"В карте больше {MAX_NODES} узлов, для выгрузки это слишком много")
        children = node.get("children") or []
        if not isinstance(children, list):
            raise ExportError("Карта повреждена: дети узла должны быть списком")
        return {"text": _clean(node.get("text")), "children": [walk(c, depth + 1) for c in children]}

    return walk(raw, 0)


# --------------------------------------------------------------------------- раскладка

_FONTS: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _font(size: float, bold: bool) -> ImageFont.FreeTypeFont:
    key = (round(size * 4), bold)
    if key not in _FONTS:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        try:
            _FONTS[key] = ImageFont.truetype(str(FONT_DIR / name), size=size)
        except OSError as exc:
            raise ExportError("Не найден шрифт для карты (memory_desk/fonts)") from exc
    return _FONTS[key]


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    """Перенос по словам; слово длиннее строки режется по символам; не больше MAX_LINES строк."""
    lines: list[str] = []
    line = ""

    def push(value: str) -> None:
        lines.append(value)

    for word in text.split(" "):
        while font.getlength(word) > width:  # очень длинное слово
            cut = len(word) - 1
            while cut > 1 and font.getlength(word[:cut]) > width:
                cut -= 1
            if line:
                push(line)
                line = ""
            push(word[:cut])
            word = word[cut:]
        candidate = word if not line else line + " " + word
        if font.getlength(candidate) <= width:
            line = candidate
        else:
            push(line)
            line = word
    if line:
        push(line)
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES]
        last = lines[-1]
        while last and font.getlength(last + "…") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines or ["—"]


@dataclass
class Box:
    text: str
    depth: int
    branch: int  # индекс цвета ветки; у корня -1
    lines: list[str]
    size: float
    bold: bool
    w: float
    h: float
    line_h: float
    x: float = 0.0
    y: float = 0.0  # верх
    children: list["Box"] = field(default_factory=list)

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Layout:
    root: Box
    width: float
    height: float
    boxes: list[Box]


def layout(tree: dict) -> Layout:
    """Дерево слева направо: колонка на уровень, родитель по центру своих детей."""
    boxes: list[Box] = []

    def make(node: dict, depth: int, branch: int) -> Box:
        size, bold, wrap = LEVELS.get(depth, DEEP)
        font = _font(size, bold)
        lines = _wrap(node["text"], font, wrap)
        line_h = round(size * 1.32)
        w = max(font.getlength(l) for l in lines) + 2 * PAD_X
        h = line_h * len(lines) + 2 * PAD_Y
        box = Box(node["text"], depth, branch, lines, size, bold, math.ceil(w), h, line_h)
        boxes.append(box)
        for i, child in enumerate(node["children"]):
            box.children.append(make(child, depth + 1, (i % len(PALETTE)) if depth == 0 else branch))
        return box

    root = make(tree, 0, -1)

    # ширина колонок
    col_w: dict[int, float] = {}
    for b in boxes:
        col_w[b.depth] = max(col_w.get(b.depth, 0), b.w)
    col_x: dict[int, float] = {}
    x = MARGIN
    for d in sorted(col_w):
        col_x[d] = x
        x += col_w[d] + COL_GAP

    def span(b: Box) -> float:
        if not b.children:
            return b.h
        return max(b.h, sum(span(c) for c in b.children) + ROW_GAP * (len(b.children) - 1))

    def place(b: Box, top: float) -> None:
        total = span(b)
        b.x = col_x[b.depth]
        if b.children:
            used = sum(span(c) for c in b.children) + ROW_GAP * (len(b.children) - 1)
            cursor = top + (total - used) / 2
            for c in b.children:
                place(c, cursor)
                cursor += span(c) + ROW_GAP
            first, last = b.children[0], b.children[-1]
            b.y = (first.cy + last.cy) / 2 - b.h / 2
        else:
            b.y = top + (total - b.h) / 2

    total_h = span(root)
    place(root, MARGIN)
    width = x - COL_GAP + MARGIN
    height = total_h + 2 * MARGIN
    # корень мог оказаться выше верхнего поля, если у него высота больше, чем у детей
    low = min(b.y for b in boxes)
    if low < MARGIN:
        shift = MARGIN - low
        for b in boxes:
            b.y += shift
        height += shift
    return Layout(root, math.ceil(width), math.ceil(height), boxes)


# --------------------------------------------------------------------------- цвета и формы

def _hex(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _mix(color: str, amount: float) -> str:
    """Оттенок цвета: `amount` — доля цвета, остальное белый."""
    r, g, b = _hex(color)
    return "#%02x%02x%02x" % tuple(round(255 - (255 - c) * amount) for c in (r, g, b))


def _colors(box: Box) -> tuple[str, str, str, str]:
    """(заливка, цвет текста, рамка, цвет связи) узла."""
    if box.depth == 0:
        return ROOT_FILL, "#ffffff", ROOT_FILL, ROOT_FILL
    base = PALETTE[box.branch]
    if box.depth == 1:
        return base, "#ffffff", base, base
    return _mix(base, 0.12), INK, _mix(base, 0.55), base


def _edge(parent: Box, child: Box) -> tuple[float, float, float, float, float, float, float, float]:
    x1, y1 = parent.x + parent.w, parent.cy
    x2, y2 = child.x, child.cy
    mid = (x1 + x2) / 2
    return x1, y1, mid, y1, mid, y2, x2, y2


def _radius(box: Box) -> float:
    return 12 if box.depth <= 1 else 9


def _baseline(box: Box, i: int) -> float:
    return box.y + PAD_Y + box.line_h * i + box.line_h / 2 + box.size * 0.35


# --------------------------------------------------------------------------- PNG

def _bezier(p: tuple[float, ...], steps: int = 40) -> list[tuple[float, float]]:
    x1, y1, cx1, cy1, cx2, cy2, x2, y2 = p
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        pts.append((u ** 3 * x1 + 3 * u * u * t * cx1 + 3 * u * t * t * cx2 + t ** 3 * x2,
                    u ** 3 * y1 + 3 * u * u * t * cy1 + 3 * u * t * t * cy2 + t ** 3 * y2))
    return pts


def render_png(tree: dict) -> bytes:
    """PNG шириной не меньше 2000 пикселей. Рисуется в двойном размере и сжимается, чтобы края были гладкими."""
    lay = layout(tree)
    scale = max(2.0, MIN_PNG_WIDTH / lay.width)
    if lay.width * lay.height * scale * scale > MAX_PNG_PIXELS:
        scale = max(1.0, math.sqrt(MAX_PNG_PIXELS / (lay.width * lay.height)))
    final = (math.ceil(lay.width * scale), math.ceil(lay.height * scale))
    ss = 2  # во сколько раз рисуем крупнее
    k = scale * ss
    image = Image.new("RGB", (final[0] * ss, final[1] * ss), SURFACE)
    draw = ImageDraw.Draw(image)

    def edges(b: Box) -> None:
        for c in b.children:
            pts = [(x * k, y * k) for x, y in _bezier(_edge(b, c))]
            width = round((3 if b.depth == 0 else 2) * k)
            draw.line(pts, fill=_colors(c)[3], width=width, joint="curve")
            r = width / 2
            for x, y in (pts[0], pts[-1]):
                draw.ellipse((x - r, y - r, x + r, y + r), fill=_colors(c)[3])
            edges(c)

    edges(lay.root)
    for b in lay.boxes:
        fill, ink, stroke, _ = _colors(b)
        draw.rounded_rectangle((b.x * k, b.y * k, (b.x + b.w) * k, (b.y + b.h) * k), radius=_radius(b) * k,
                               fill=fill, outline=stroke, width=max(1, round(1.5 * k)))
        font = _font(b.size * k, b.bold)
        for i, line in enumerate(b.lines):
            draw.text(((b.x + PAD_X) * k, _baseline(b, i) * k), line, font=font, fill=ink, anchor="ls")
    image = image.resize(final, Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True, dpi=(144, 144))
    return buf.getvalue()


# --------------------------------------------------------------------------- вход

def export(fmt: str, title: str, tree: object, name: str | None = None) -> tuple[bytes, str, str]:
    """Возвращает (содержимое, имя файла, тип)."""
    if fmt not in FORMATS:
        raise ExportError("Карта выгружается только в PNG")
    clean = parse_tree(tree)
    title = " ".join((title or "").split()) or clean["text"]
    stem = safe_filename(re.sub(r"\.png$", "", name or f"Карта — {title}", flags=re.I))
    return render_png(clean), f"{stem}.png", FORMATS[fmt][0]
