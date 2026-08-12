"""
Generates the home-screen / PWA icons for the GitHub Pages build.
Run after any brand-color change:

    python3 generate_icons.py

Writes docs/icon-180.png (apple-touch-icon), docs/icon-192.png and
docs/icon-512.png (manifest.json), and docs/favicon.png.
"""

from __future__ import annotations
import tempfile
from pathlib import Path
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"


def _extract_ttf() -> str:
    font = TTFont(ROOT / "fonts" / "bebasneue.subset.woff2")
    font.flavor = None
    fd, path = tempfile.mkstemp(suffix=".ttf")
    font.save(path)
    return path


FONT_PATH = _extract_ttf()

BG_TOP = (139, 63, 240)  # #8B3FF0 --accent
BG_BOTTOM = (94, 40, 176)
MARK_COLOR = (255, 255, 255)


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), BG_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / size
        r = round(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = round(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = round(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b))

    font_size = round(size * 0.62)
    font = ImageFont.truetype(FONT_PATH, font_size)
    text = "F"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pos = ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1] - size * 0.02)
    draw.text(pos, text, font=font, fill=MARK_COLOR)
    return img


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    make_icon(180).save(DOCS / "icon-180.png")
    make_icon(192).save(DOCS / "icon-192.png")
    make_icon(512).save(DOCS / "icon-512.png")
    make_icon(64).save(DOCS / "favicon.png")
    print("Wrote icons to docs/")


if __name__ == "__main__":
    main()
