"""Safely simulate one schedule-change alert for exactly one active Telegram chat.

This script does NOT modify D1 or the real saved schedule snapshot. It flips one
slot only in an in-memory copy, exercises the same change predicate and photo
delivery helper as the production background refresh, then sends one marked
test PNG to the selected chat.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from bot import _caption, schedule_snapshot_changed, send_schedule_change_photo
from cloudflare_d1 import D1Client
from render import render_schedule_png
from telegram_api import Telegram
from toe_api import fetch_schedule

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("simulate_schedule_change")


def main() -> None:
    chat_id = os.environ.get("CHAT_ID", "").strip()
    if not chat_id or not chat_id.lstrip("-").isdigit():
        raise SystemExit("CHAT_ID має бути числовим Telegram chat ID.")

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not telegram_token:
        raise SystemExit("Не задано TELEGRAM_BOT_TOKEN.")
    timezone = ZoneInfo(os.environ.get("TIMEZONE", "Europe/Kyiv"))

    # Read-only lookup: deliberately no UPDATE/INSERT/DELETE against D1.
    d1 = D1Client()
    rows = d1.query(
        "SELECT chat_id, active, address_key, address_label, group_name "
        "FROM users WHERE chat_id = ? LIMIT 1",
        [chat_id],
    )
    if not rows:
        raise RuntimeError("Для цього chat_id не знайдено підписку в D1. Перевірте, що ввели власний ID.")
    row = rows[0]
    if int(row.get("active") or 0) != 1:
        raise RuntimeError("Підписка цього чату не активна. Тестове повідомлення не надсилалося.")

    address_key = str(row.get("address_key") or "")
    address_label = str(row.get("address_label") or "")
    group = str(row.get("group_name") or "")
    if not address_key or not group:
        raise RuntimeError("У цього чату не налаштовано адресу або чергу. Тестове повідомлення не надсилалося.")

    http = requests.Session()
    http.headers.update({"User-Agent": "TOE-Public-Outage-Bot-test/2.4"})
    schedules = fetch_schedule(address_key, group, timezone, session=http)
    if not schedules:
        raise RuntimeError("TOE не повернув поточний графік. Тестове повідомлення не надсилалося.")

    current_snapshot = {day: item["slots"] for day, item in sorted(schedules.items())}
    # Manufacture a different prior state in memory only.
    simulated_previous = dict(current_snapshot)
    first_day = next(iter(sorted(simulated_previous)))
    chars = list(simulated_previous[first_day])
    if len(chars) != 48:
        raise RuntimeError("Отримано некоректну довжину графіка; тестове фото не надсилалося.")
    slot_index = next((i for i, value in enumerate(chars) if value in ("0", "1", "2")), None)
    if slot_index is None:
        raise RuntimeError("Немає придатного 30-хвилинного інтервалу для тесту.")
    old_value = chars[slot_index]
    chars[slot_index] = "0" if old_value != "0" else "1"
    simulated_previous[first_day] = "".join(chars)

    if not schedule_snapshot_changed(simulated_previous, current_snapshot):
        raise RuntimeError("Тестова зміна не пройшла comparator; Telegram не викликався.")

    now = datetime.now(timezone)
    png = render_schedule_png(schedules, address_label, group, now)
    status = (
        "🧪 <b>ТЕСТОВЕ СПОВІЩЕННЯ ПРО ЗМІНУ ГРАФІКА</b>\n"
        "Система успішно виявила штучну зміну одного 30-хвилинного інтервалу "
        "та перевірила надсилання нового PNG.\n"
        "<b>Це тест:</b> реальний графік на сайті не змінювався, "
        "збережений знімок у базі не змінювався."
    )
    caption = _caption(
        {"address_label": address_label, "group": group},
        now,
        status,
    )
    tg = Telegram(telegram_token)
    message_id = send_schedule_change_photo(tg, chat_id, png, caption)
    log.info(
        "SIMULATED CHANGE TEST SUCCESS: chat_id=%s, address_key=%s, group=%s, "
        "day=%s, slot=%s, synthetic_before=%s, actual_now=%s, message_id=%s",
        chat_id, address_key, group, first_day, slot_index,
        old_value, current_snapshot[first_day][slot_index], message_id,
    )
    log.info("D1 was read only; no real schedule snapshot or subscription data was modified.")


if __name__ == "__main__":
    main()
