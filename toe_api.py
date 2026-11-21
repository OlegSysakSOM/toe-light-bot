"""Клієнт API сайту poweron.toe.com.ua (Тернопільобленерго) + порівняння графіків."""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("toe")

API_BASE = "https://api-poweron.toe.com.ua/api"

# Статуси півгодинних слотів у відповіді сайту: "0" світло є, "1" немає, "10" можливе відключення.
# У коді працюємо з односимвольними кодами, щоб графік дня був рядком із 48 символів.
ON, OFF, MAYBE, UNKNOWN = "0", "1", "2", "3"
_RAW_TO_CODE = {"0": ON, "1": OFF, "10": MAYBE}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://poweron.toe.com.ua",
    "Referer": "https://poweron.toe.com.ua/",
}


@dataclass(frozen=True)
class DaySchedule:
    day: date
    slots: str  # 48 символів: ON / OFF / MAYBE / UNKNOWN, слот = 30 хв, починаючи з 00:00
    created: str = ""  # dateCreate з API (для логів)


# ───────────────────────────── завантаження ─────────────────────────────

def fetch_schedule(
    address_key: str,
    group: str,
    tz: ZoneInfo,
    session: requests.Session | None = None,
    timeout: int = 20,
) -> dict[date, DaySchedule]:
    """Повертає графіки на сьогодні та завтра (тільки ті дати, які є на сайті)."""
    sess = session or requests.Session()
    now = datetime.now(tz)
    today = now.date()

    # Так само, як це робить фронтенд сайту. Ключ адреси передається у base64 в X-debug-key.
    after = (now - timedelta(days=1)).strftime("%Y-%m-%dT12:00:00+00:00")
    before = (now + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59+00:00")
    headers = dict(HEADERS)
    headers["X-debug-key"] = base64.b64encode(address_key.encode("utf-8")).decode("ascii")
    params = {
        "before": before,
        "after": after,
        "group[]": group,
        "time": address_key.replace("/", ""),
    }

    resp = sess.get(f"{API_BASE}/a_gpv_g", params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()

    if isinstance(payload, list):
        members = payload
    else:
        members = payload.get("hydra:member") or payload.get("member") or []

    found: dict[date, DaySchedule] = {}
    for member in members:
        parsed = _parse_member(member, group)
        if parsed is None:
            continue
        if not (today <= parsed.day <= today + timedelta(days=1)):
            continue
        prev = found.get(parsed.day)
        # якщо на одну дату є кілька версій — беремо найсвіжішу за dateCreate
        if prev is None or parsed.created >= prev.created:
            found[parsed.day] = parsed
    return found


def _parse_member(member: dict, group: str) -> DaySchedule | None:
    data = member.get("dataJson")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None

    group_data = data.get(group)
    if group_data is None:
        for key, value in data.items():
            if str(key).strip() == group.strip():
                group_data = value
                break
    if not isinstance(group_data, dict):
        return None
    times = group_data.get("times") or {}

    raw_date = str(member.get("dateGraph", ""))[:10]
    try:
        day = date.fromisoformat(raw_date)
    except ValueError:
        return None

    slots = []
    for hour in range(24):
        for minute in (0, 30):
            raw = str(times.get(f"{hour:02d}:{minute:02d}", "0")).strip()
            code = _RAW_TO_CODE.get(raw)
            if code is None:
                log.warning("Невідомий статус слота %s %02d:%02d: %r", day, hour, minute, raw)
                code = UNKNOWN
            slots.append(code)
    return DaySchedule(day=day, slots="".join(slots), created=str(member.get("dateCreate", "")))


# ───────────────────────────── допоміжне ─────────────────────────────

def fmt_slot(index: int) -> str:
    minutes = index * 30
    return "24:00" if minutes >= 1440 else f"{minutes // 60:02d}:{minutes % 60:02d}"


def fmt_range(start: int, end: int) -> str:
    return f"{fmt_slot(start)}–{fmt_slot(end)}"


def fmt_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} год {m} хв"
    return f"{h} год" if h else f"{m} хв"


def intervals_of(slots: str) -> list[tuple[str, int, int]]:
    """Суцільні проміжки відключень: (статус, перший_слот, слот_після_останнього)."""
    out: list[tuple[str, int, int]] = []
    i = 0
    while i < len(slots):
        j = i
        while j < len(slots) and slots[j] == slots[i]:
            j += 1
        if slots[i] in (OFF, MAYBE):
            out.append((slots[i], i, j))
        i = j
    return out


def count_minutes(slots: str, code: str) -> int:
    return slots.count(code) * 30


# ───────────────────────────── порівняння ─────────────────────────────

def snapshot_of(days: dict[date, DaySchedule]) -> dict[str, str]:
    return {d.isoformat(): s.slots for d, s in sorted(days.items())}


def _ranges(indexes: list[int]) -> str:
    parts, start, prev = [], None, None
    for idx in indexes:
        if start is None:
            start = prev = idx
        elif idx == prev + 1:
            prev = idx
        else:
            parts.append(fmt_range(start, prev + 1))
            start = prev = idx
    if start is not None:
        parts.append(fmt_range(start, prev + 1))
    return ", ".join(parts)


def diff_snapshots(old: dict[str, str], new: dict[str, str], today: date) -> list[str]:
    """Список людських описів змін. Порожній список = змін немає.

    Дати, які вже минули, ігноруються (опівночі вчорашній день зникає з відповіді — це не зміна).
    """
    lines: list[str] = []
    for iso, slots in new.items():
        label = date.fromisoformat(iso).strftime("%d.%m")
        if iso not in old:
            lines.append(f"📅 Опубліковано графік на {label}")
            continue
        before = old[iso]
        if before == slots:
            continue
        lines_before = len(lines)
        for code, icon, word in ((OFF, "🔴", "відключення"), (MAYBE, "🟡", "можливе відключення")):
            added = [i for i in range(len(slots)) if slots[i] == code and before[i] != code]
            removed = [i for i in range(len(slots)) if before[i] == code and slots[i] != code]
            if added:
                lines.append(f"{icon} {label}: додано {word} {_ranges(added)}")
            if removed:
                lines.append(f"🟢 {label}: прибрано {word} {_ranges(removed)}")
        if len(lines) == lines_before:  # змінилися лише «невідомі» слоти тощо
            lines.append(f"✏️ {label}: графік змінено")
    for iso in old:
        if iso not in new and date.fromisoformat(iso) >= today:
            lines.append(f"⚠️ Графік на {date.fromisoformat(iso).strftime('%d.%m')} зник із сайту")
    return lines
