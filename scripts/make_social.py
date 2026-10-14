"""Картинка для предпросмотра репозитория (GitHub → Settings → Social preview): 1280×640."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "memory_desk" / "fonts"
W, H = 1280, 640
img = Image.new("RGB", (W, H), (24, 25, 28))
d = ImageDraw.Draw(img)
icon = Image.open(ROOT / "macos" / "Resources" / "AppIcon.png").convert("RGBA").resize((200, 200))
img.paste(icon, (90, 100), icon)
big = ImageFont.truetype(str(FONTS / "DejaVuSans-Bold.ttf"), 78)
mid = ImageFont.truetype(str(FONTS / "DejaVuSans.ttf"), 34)
small = ImageFont.truetype(str(FONTS / "DejaVuSans.ttf"), 26)
d.text((330, 112), "Memory Desk", font=big, fill=(240, 240, 242))
d.text((332, 214), "Локальный ИИ-кабинет встреч", font=mid, fill=(200, 202, 208))
d.text((90, 370), "Записи → стенограммы → задачи, решения, люди", font=mid, fill=(240, 240, 242))
d.text((90, 424), "Поиск по смыслу и чат по вашим встречам.", font=mid, fill=(200, 202, 208))
d.text((90, 478), "Whisper + Ollama + Obsidian. Всё на вашем Mac, без облака.", font=mid, fill=(200, 202, 208))
d.text((90, 560), "github.com/vgerasin/Memory-Desk  ·  MIT", font=small, fill=(140, 142, 150))
out = ROOT / "docs" / "img" / "social.png"
img.save(out, optimize=True)
print(out)
