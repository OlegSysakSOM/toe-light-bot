"""Render a legible, rounded-corner outage schedule poster with Pillow."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

W = 1080
BG = "#0B1730"
PANEL = "#142541"
TEXT = "#F4F7FB"
MUTED = "#AFC0D7"
GREEN = "#178A65"
RED = "#D94F63"
YELLOW = "#D69B2D"
GRAY = "#52647D"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _text_fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int,
              bold: bool = False, min_size: int = 13) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for size in range(start, min_size - 1, -1):
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _font(min_size, bold)


def _slot_text(index: int) -> str:
    mins = index * 30
    return f"{mins // 60:02d}:{mins % 60:02d}–{(mins + 30) // 60:02d}:{(mins + 30) % 60:02d}"


def _status(code: str) -> tuple[str, str]:
    if code == "0":
        return GREEN, "Є СВІТЛО"
    if code == "1":
        return RED, "НЕМАЄ"
    if code == "2":
        return YELLOW, "МОЖЛИВО"
    return GRAY, "НЕВІДОМО"


def render_schedule_png(days: dict[str, dict[str, str]], address_label: str,
                        group: str, checked_at: datetime) -> bytes:
    """Create a 1080px-wide poster for up to two days of 30-minute periods."""
    sorted_days = sorted(days.items())[:2]
    if not sorted_days:
        raise ValueError("Немає днів для побудови зображення")

    # Header 210px + 2 day panels (585px each) + footer.
    height = 250 + len(sorted_days) * 600 + 125
    image = Image.new("RGB", (W, height), BG)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((34, 28, W - 34, 220), radius=34, fill=PANEL)
    draw.text((66, 55), "ГРАФІК ВІДКЛЮЧЕНЬ", font=_font(30, True), fill=MUTED)
    address = address_label
    address_font = _text_fit(draw, address, W - 132, 43, True, 23)
    draw.text((66, 104), address, font=address_font, fill=TEXT)
    draw.rounded_rectangle((66, 165, 285, 207), radius=18, fill="#243D61")
    draw.text((84, 172), f"ЧЕРГА {group}", font=_font(23, True), fill=TEXT)
    draw.text((W - 360, 176), "ІНТЕРВАЛИ 30 ХВ", font=_font(17, True), fill=MUTED)

    y = 242
    for day_iso, schedule in sorted_days:
        slots = str(schedule.get("slots", ""))
        if len(slots) != 48:
            slots = (slots + "3" * 48)[:48]
        try:
            day_label = datetime.strptime(day_iso, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            day_label = day_iso
        draw.rounded_rectangle((34, y, W - 34, y + 578), radius=30, fill=PANEL)
        draw.text((66, y + 24), day_label, font=_font(34, True), fill=TEXT)
        draw.text((W - 330, y + 36), "ПОНЕДІЛОК–НЕДІЛЯ" if False else "ПОГОДИННО", font=_font(16, True), fill=MUTED)

        left = 62
        top = y + 92
        gap_x = 10
        gap_y = 10
        cols = 8
        card_w = (W - 124 - gap_x * (cols - 1)) // cols
        card_h = 68
        for idx, code in enumerate(slots):
            row, col = divmod(idx, cols)
            x1 = left + col * (card_w + gap_x)
            y1 = top + row * (card_h + gap_y)
            bg, label = _status(code)
            draw.rounded_rectangle((x1, y1, x1 + card_w, y1 + card_h), radius=13, fill=bg)
            t = _slot_text(idx)
            f_time = _text_fit(draw, t, card_w - 8, 15, True, 11)
            bbox = draw.textbbox((0, 0), t, font=f_time)
            draw.text((x1 + (card_w - (bbox[2] - bbox[0])) / 2, y1 + 10), t, font=f_time, fill="#FFFFFF")
            f_label = _text_fit(draw, label, card_w - 8, 14, True, 10)
            lb = draw.textbbox((0, 0), label, font=f_label)
            draw.text((x1 + (card_w - (lb[2] - lb[0])) / 2, y1 + 39), label, font=f_label, fill="#FFFFFF")

        y += 600

    footer_y = height - 95
    draw.text((58, footer_y), f"ОНОВЛЕНО: {checked_at.strftime('%d.%m.%Y  %H:%M')}  (КИЇВ)",
              font=_font(21, True), fill=TEXT)
    draw.text((58, footer_y + 38), "Дані отримані з poweron.toe.com.ua", font=_font(17), fill=MUTED)

    stream = BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()
