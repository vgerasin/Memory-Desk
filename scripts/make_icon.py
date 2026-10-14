#!/usr/bin/env python3
"""Рисует значок приложения Memory Desk: macos/Resources/AppIcon.png (1024 px) и AppIcon.icns.

Запуск: python3 scripts/make_icon.py (нужен Pillow). Тёмная плитка со скруглением в стиле macOS и красная точка записи с кольцами:
тот же знак, что в шапке кабинета и во вкладке браузера."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "macos" / "Resources"
SS = 2                      # рисуем в двойном размере и уменьшаем: гладкие края
N = 1024 * SS


def rounded_mask(size: int, box: tuple[int, int, int, int], radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask


def build() -> Image.Image:
    margin, radius = 100 * SS, 186 * SS                   # плитка 824 px на холсте 1024: как у значков macOS
    box = (margin, margin, N - margin, N - margin)
    canvas = Image.new("RGBA", (N, N), (0, 0, 0, 0))

    # мягкая тень под плиткой
    shadow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + 14 * SS, box[2], box[3] + 14 * SS), radius=radius, fill=(0, 0, 0, 110))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22 * SS)))

    # плитка: вертикальный градиент графитового цвета
    tile = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (N, N))
    px = ImageDraw.Draw(gradient)
    top, bottom = (54, 54, 60), (20, 20, 23)
    for y in range(box[1], box[3]):
        t = (y - box[1]) / (box[3] - box[1])
        px.line([(0, y), (N, y)], fill=tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    tile.paste(gradient, mask=rounded_mask(N, box, radius))
    canvas.alpha_composite(tile)

    # тонкая светлая кромка сверху
    rim = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle(box, radius=radius, outline=(255, 255, 255, 46), width=3 * SS)
    fade = Image.new("L", (N, N), 0)
    fd = ImageDraw.Draw(fade)
    for y in range(box[1], box[3]):
        fd.line([(0, y), (N, y)], fill=max(0, round(255 * (1 - (y - box[1]) / ((box[3] - box[1]) * 0.55)))))
    rim.putalpha(ImageChops.multiply(rim.getchannel("A"), fade))
    canvas.alpha_composite(rim)

    cx = cy = N // 2
    # кольца вокруг точки: «запись» и память (расходящиеся круги)
    for radius_px, alpha, width in ((330, 34, 10), (250, 62, 12)):
        ring = Image.new("RGBA", (N, N), (0, 0, 0, 0))
        r = radius_px * SS
        ImageDraw.Draw(ring).ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 69, 58, alpha * 2 // 1), width=width * SS)
        canvas.alpha_composite(ring)

    # свечение и сама точка
    glow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    g = 190 * SS
    ImageDraw.Draw(glow).ellipse((cx - g, cy - g, cx + g, cy + g), fill=(255, 69, 58, 120))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(60 * SS)))
    dot = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    r = 150 * SS
    dd = ImageDraw.Draw(dot)
    dd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 69, 58, 255))
    canvas.alpha_composite(dot)
    # блик на точке
    spec = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    sr = 118 * SS
    ImageDraw.Draw(spec).ellipse((cx - sr, cy - sr - 30 * SS, cx + sr, cy + sr - 30 * SS), fill=(255, 255, 255, 40))
    spec_mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(spec_mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    spec.putalpha(ImageChops.multiply(spec.getchannel("A"), spec_mask))
    canvas.alpha_composite(spec.filter(ImageFilter.GaussianBlur(10 * SS)))
    return canvas.resize((1024, 1024), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = build()
    icon.save(OUT / "AppIcon.png")
    icon.save(OUT / "AppIcon.icns", format="ICNS")
    print("Готово:", OUT / "AppIcon.png", OUT / "AppIcon.icns")


if __name__ == "__main__":
    main()
