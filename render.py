"""Малює картинку з графіком: заокруглені картки, насичені кольори, плитки по годинах.

Запуск `python render.py` створює demo.png на вигадних даних (без інтернету) —
зручно, щоб оцінити вигляд.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from toe_api import MAYBE, OFF, ON, DaySchedule, count_minutes, fmt_duration, fmt_range, intervals_of

# ───────────────────────────── стиль ─────────────────────────────
W = 1080           # ширина підсумкової картинки, px
SS = 2             # supersampling: малюємо у 2× і зменшуємо → гладкі округлені краї
PAD = 36
GAP = 28

BG_TOP, BG_BOT = (8, 12, 32), (28, 22, 74)
CARD, CARD_BORDER = (21, 29, 64), (48, 60, 116)
TEXT, MUTED, INK = (246, 248, 255), (153, 166, 210), (9, 14, 38)
C_ON, C_OFF, C_MAYBE, C_UNKNOWN = (44, 230, 140), (255, 77, 94), (255, 201, 60), (120, 130, 165)
HEAD_A, HEAD_B = (52, 96, 255), (139, 70, 240)
C_ALERT = (255, 160, 40)

COLORS = {ON: C_ON, OFF: C_OFF, MAYBE: C_MAYBE}

GRID_COLS, TILE_W_GAP, TILE_H, TILE_V_GAP = 8, 10, 78, 12   # 8×3: ряд = 8 годин
GRID_ROWS = 24 // GRID_COLS
CHIP_H, CHIP_GAP, CHIP_PADX = 60, 12, 26

DAYS_UK = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"]

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_FILES = {"med": "Inter-Medium.otf", "semi": "Inter-SemiBold.otf",
              "bold": "Inter-Bold.otf", "xbold": "Inter-ExtraBold.otf"}
FONT_FALLBACKS = [
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


@lru_cache(maxsize=None)
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in [FONT_DIR / FONT_FILES[weight], *map(Path, FONT_FALLBACKS)]:
        try:
            return ImageFont.truetype(str(candidate), int(size * SS))
        except OSError:
            continue
    raise RuntimeError("Не знайдено шрифт із кирилицею. Покладіть Inter-*.otf у папку fonts/")


def text_w(text: str, weight: str, size: int) -> float:
    return font(weight, size).getlength(text) / SS


# ───────────────────────────── дані для малювання ─────────────────────────────
@dataclass
class DayView:
    day: date
    title: str
    schedule: DaySchedule | None


def build_views(days: dict[date, DaySchedule], today: date) -> list[DayView]:
    tomorrow = today + timedelta(days=1)
    return [
        DayView(today, "Сьогодні", days.get(today)),
        DayView(tomorrow, "Завтра", days.get(tomorrow)),
    ]


# ───────────────────────────── «олівець» ─────────────────────────────
class Pen:
    def __init__(self, w: int, h: int):
        self.img = Image.new("RGB", (w * SS, h * SS), BG_TOP)
        self.d = ImageDraw.Draw(self.img)
        total = h * SS
        for y in range(total):  # вертикальний градієнт фону (без зайвих повнорозмірних копій у пам'яті)
            k = y / max(1, total - 1)
            color = tuple(round(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * k) for i in range(3))
            self.d.line([(0, y), (w * SS, y)], fill=color)

    @staticmethod
    def s(v: float) -> int:
        return int(round(v * SS))

    def box(self, b):
        return tuple(self.s(v) for v in b)

    def rrect(self, b, r, fill=None, outline=None, width=0):
        x0, y0, x1, y1 = self.box(b)
        self.d.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=self.s(r), fill=fill,
                                 outline=outline, width=self.s(width) if outline else 0)

    def grad_rrect(self, b, r, c0, c1):
        x0, y0, x1, y1 = self.box(b)
        w, h = x1 - x0, y1 - y0
        g = Image.linear_gradient("L").rotate(90, expand=True).resize((w, h))
        layer = Image.composite(Image.new("RGB", (w, h), c1), Image.new("RGB", (w, h), c0), g)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=self.s(r), fill=255)
        self.img.paste(layer, (x0, y0), mask)

    def tile(self, b, r, c_left, c_right):
        """Плитка-година: ліва половина = :00–:30, права = :30–:00."""
        x0, y0, x1, y1 = self.box(b)
        w, h = x1 - x0, y1 - y0
        layer = Image.new("RGB", (w, h), c_left)
        if c_right != c_left:
            ImageDraw.Draw(layer).rectangle((w // 2, 0, w, h), fill=c_right)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=self.s(r), fill=255)
        self.img.paste(layer, (x0, y0), mask)

    def text(self, xy, t, f, fill, anchor="lm"):
        self.d.text((self.s(xy[0]), self.s(xy[1])), t, font=f, fill=fill, anchor=anchor)

    def circle(self, cx, cy, r, fill):
        self.d.ellipse((self.s(cx - r), self.s(cy - r), self.s(cx + r), self.s(cy + r)), fill=fill)

    def polygon(self, pts, fill):
        self.d.polygon([(self.s(x), self.s(y)) for x, y in pts], fill=fill)

    def line(self, pts, fill, width):
        sp = [(self.s(x), self.s(y)) for x, y in pts]
        self.d.line(sp, fill=fill, width=self.s(width), joint="curve")
        r = self.s(width) / 2
        for x, y in (sp[0], sp[-1]):
            self.d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


# ───────────────────────────── розкладка ─────────────────────────────
def _chips_for(sched: DaySchedule) -> list[tuple[str, tuple]]:
    items = []
    for code, a, b in intervals_of(sched.slots):
        label = f"{fmt_range(a, b)} · {fmt_duration((b - a) * 30)}"
        items.append((label, COLORS[code]))
    if not items:
        items.append(("Відключень не заплановано", C_ON))
    return items


def _chip_rows(items, max_w):
    rows, cur, x = [], [], 0.0
    for text, color in items:
        w = text_w(text, "bold", 28) + 2 * CHIP_PADX
        if cur and x + w > max_w:
            rows.append(cur)
            cur, x = [], 0.0
        cur.append((x, w, text, color))
        x += w + CHIP_GAP
    if cur:
        rows.append(cur)
    return rows


GRID_H = GRID_ROWS * TILE_H + (GRID_ROWS - 1) * TILE_V_GAP
HEAD_BLOCK = 150           # від верху картки до початку сітки
HEADER_H, FOOTER_H = 190, 236
INNER_W = W - 2 * PAD - 2 * 36


def _day_height(view: DayView) -> int:
    if view.schedule is None:
        return 150
    rows = _chip_rows(_chips_for(view.schedule), INNER_W)
    chips_h = len(rows) * CHIP_H + (len(rows) - 1) * CHIP_GAP
    return HEAD_BLOCK + GRID_H + 28 + chips_h + 36


# ───────────────────────────── блоки ─────────────────────────────
def _draw_header(p: Pen, y: int, address: str, group: str):
    p.grad_rrect((PAD, y, W - PAD, y + HEADER_H), 38, HEAD_A, HEAD_B)
    cx, cy = PAD + 36 + 46, y + HEADER_H // 2
    p.circle(cx, cy, 46, (255, 255, 255))
    p.polygon([(cx + 6, cy - 30), (cx - 22, cy + 4), (cx - 3, cy + 4),
               (cx - 9, cy + 30), (cx + 22, cy - 6), (cx + 3, cy - 6)], (255, 170, 0))

    badge = f"Черга {group}"
    bw = text_w(badge, "xbold", 34) + 56
    bx1 = W - PAD - 36
    p.rrect((bx1 - bw, cy - 38, bx1, cy + 38), 38, fill=(255, 255, 255))
    p.text((bx1 - bw / 2, cy), badge, font("xbold", 34), HEAD_A, "mm")

    tx = cx + 46 + 28
    p.text((tx, cy - 24), "Графік відключень", font("xbold", 46), TEXT)
    size, avail = 28, bx1 - bw - 24 - tx
    while text_w(address, "med", size) > avail and size > 18:
        size -= 1
    p.text((tx, cy + 28), address, font("med", size), (226, 232, 255))


def _draw_day(p: Pen, y: int, view: DayView, now: datetime) -> int:
    h = _day_height(view)
    x0, x1 = PAD, W - PAD
    p.rrect((x0, y, x1, y + h), 38, fill=CARD, outline=CARD_BORDER, width=2)
    ix = x0 + 36

    date_line = f"{view.day.strftime('%d.%m.%Y')} · {DAYS_UK[view.day.weekday()]}"
    p.text((ix, y + 58), view.title, font("xbold", 50), TEXT)
    p.text((ix, y + 104), date_line, font("med", 28), MUTED)

    sched = view.schedule
    if sched is None:
        pill, color = "Графік ще не опублікований", C_UNKNOWN
    else:
        off_min = count_minutes(sched.slots, OFF)
        if off_min:
            pill, color = f"Без світла: {fmt_duration(off_min)}", C_OFF
        elif count_minutes(sched.slots, MAYBE):
            pill, color = "Можливі відключення", C_MAYBE
        else:
            pill, color = "Відключень немає", C_ON
    pw = text_w(pill, "bold", 29) + 52
    p.rrect((x1 - 36 - pw, y + 40, x1 - 36, y + 100), 30, fill=color)
    p.text((x1 - 36 - pw / 2, y + 70), pill, font("bold", 29), INK, "mm")
    if sched is None:
        return h

    # сітка 8×3: ряд = 8 годин доби
    tile_w = (INNER_W - (GRID_COLS - 1) * TILE_W_GAP) / GRID_COLS
    gy = y + HEAD_BLOCK
    now_hour = now.hour if view.day == now.date() else None
    marker = None
    for hour in range(24):
        r, c = divmod(hour, GRID_COLS)
        tx0 = ix + c * (tile_w + TILE_W_GAP)
        ty0 = gy + r * (TILE_H + TILE_V_GAP)
        a, b = sched.slots[hour * 2], sched.slots[hour * 2 + 1]
        p.tile((tx0, ty0, tx0 + tile_w, ty0 + TILE_H), 22,
               COLORS.get(a, C_UNKNOWN), COLORS.get(b, C_UNKNOWN))
        p.text((tx0 + tile_w / 2, ty0 + TILE_H / 2), f"{hour:02d}–{hour + 1:02d}",
               font("xbold", 30), INK, "mm")
        if hour == now_hour:
            marker = (tx0, ty0, tile_w)
    if marker:  # біле кільце + позначка «ЗАРАЗ» навколо поточної години
        tx0, ty0, tw = marker
        p.rrect((tx0 - 5, ty0 - 5, tx0 + tw + 5, ty0 + TILE_H + 5), 26, outline=(255, 255, 255), width=5)
        p.rrect((tx0 + tw / 2 - 40, ty0 - 17, tx0 + tw / 2 + 40, ty0 + 13), 15, fill=(255, 255, 255))
        p.text((tx0 + tw / 2, ty0 - 2), "ЗАРАЗ", font("xbold", 17), INK, "mm")

    # чіпи з проміжками
    cy = gy + GRID_H + 28
    for row in _chip_rows(_chips_for(sched), INNER_W):
        for off, w, text, color in row:
            p.rrect((ix + off, cy, ix + off + w, cy + CHIP_H), CHIP_H / 2, fill=color)
            p.text((ix + off + w / 2, cy + CHIP_H / 2), text, font("bold", 28), INK, "mm")
        cy += CHIP_H + CHIP_GAP
    return h


def _draw_footer(p: Pen, y: int, status: str, updated: datetime, interval_min: int):
    x0, x1 = PAD, W - PAD
    ok = status == "nochange"
    accent = C_ON if ok else C_ALERT
    p.rrect((x0, y, x1, y + FOOTER_H), 38, fill=CARD, outline=accent, width=3)

    cx, cy = x0 + 36 + 34, y + 62
    p.circle(cx, cy, 34, accent)
    if ok:
        p.line([(cx - 14, cy + 1), (cx - 4, cy + 11), (cx + 15, cy - 11)], INK, 8)
    else:
        p.rrect((cx - 4, cy - 18, cx + 4, cy + 4), 4, fill=INK)
        p.circle(cx, cy + 15, 5, INK)

    title = "Змін немає" if ok else "Графік оновлено"
    p.text((cx + 34 + 24, cy - 14), title, font("xbold", 40), TEXT)
    p.text((cx + 34 + 24, cy + 24), f"Оновлено о {updated.strftime('%H:%M')}  ·  перевірка кожні {interval_min} хв",
           font("med", 26), MUTED)

    # легенда
    lx, ly = x0 + 36, y + FOOTER_H - 82
    for color, label in ((C_ON, "Є світло"), (C_OFF, "Немає світла"), (C_MAYBE, "Можливе відключення")):
        p.rrect((lx, ly - 11, lx + 22, ly + 11), 7, fill=color)
        p.text((lx + 34, ly), label, font("semi", 25), TEXT)
        lx += 34 + text_w(label, "semi", 25) + 36
    p.text((x0 + 36, y + FOOTER_H - 38), "Кожна плитка — година; половинки — по 30 хв", font("med", 23), MUTED)


# ───────────────────────────── головна функція ─────────────────────────────
def render_png(views: list[DayView], address: str, group: str, status: str,
               now: datetime, interval_min: int = 10) -> bytes:
    """status: 'nochange' (зелена позначка «Змін немає») або 'changed' / 'first'."""
    total_h = PAD + HEADER_H + GAP + sum(_day_height(v) + GAP for v in views) + FOOTER_H + PAD
    p = Pen(W, total_h)
    y = PAD
    _draw_header(p, y, address, group)
    y += HEADER_H + GAP
    for view in views:
        y += _draw_day(p, y, view, now) + GAP
    _draw_footer(p, y, status, now, interval_min)

    img = p.img.resize((W, total_h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _demo_slots(off: list[tuple[int, int]], maybe: list[tuple[int, int]] = ()) -> str:
    s = [ON] * 48
    for a, b in maybe:
        s[a:b] = [MAYBE] * (b - a)
    for a, b in off:
        s[a:b] = [OFF] * (b - a)
    return "".join(s)


if __name__ == "__main__":
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Kyiv")
    now = datetime.now(tz)
    today = now.date()
    demo = {
        today: DaySchedule(today, _demo_slots([(16, 24), (36, 44)], [(24, 28), (10, 13)])),
        today + timedelta(days=1): DaySchedule(today + timedelta(days=1),
                                                _demo_slots([(4, 10), (21, 27), (38, 46)])),
    }
    for status in ("nochange", "changed"):
        png = render_png(build_views(demo, today), "Приклад адреси", "5.1", status, now)
        Path(f"demo_{status}.png").write_bytes(png)
        print(f"demo_{status}.png", len(png) // 1024, "КБ")
