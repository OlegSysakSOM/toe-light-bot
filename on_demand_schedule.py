"""Send the current schedule as a PNG after the Telegram button request."""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from bot import _caption
from cloudflare_d1 import D1Client
from render import render_schedule_png
from telegram_api import Telegram
from toe_api import fetch_schedule

log = logging.getLogger("on_demand_schedule")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", default=os.environ.get("CHAT_ID", "").strip())
    chat_id = str(parser.parse_args().chat_id).strip()
    if not chat_id:
        raise SystemExit("Не передано CHAT_ID для запиту графіка.")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задано TELEGRAM_BOT_TOKEN у GitHub Secrets.")

    tz = ZoneInfo(os.environ.get("TIMEZONE", "Europe/Kyiv"))
    d1 = D1Client()
    rows = d1.query(
        "SELECT chat_id, address_key, address_label, group_name "
        "FROM users WHERE chat_id = ? LIMIT 1", [chat_id],
    )
    if not rows:
        raise RuntimeError("Не знайдено підписку цього Telegram-чату.")
    row = rows[0]
    address_key = str(row.get("address_key") or "")
    group = str(row.get("group_name") or "")
    address = str(row.get("address_label") or "")
    if not address_key or not group:
        raise RuntimeError("Спочатку підключіть адресу в боті.")

    http = requests.Session()
    http.headers.update({"User-Agent": "TOE-Public-Outage-Bot/2.4"})
    schedules = fetch_schedule(address_key, group, tz, session=http)
    if not schedules:
        raise RuntimeError("Сайт Тернопільобленерго ще не опублікував графік на сьогодні або завтра.")

    now = datetime.now(tz)
    png = render_schedule_png(schedules, address, group, now)
    caption = _caption(
        {"address_label": address, "group": group},
        now, "📲 <b>Актуальний графік — щойно отримано із сайту.</b>",
    )
    tg = Telegram(token)
    message_id = tg.send_photo(chat_id, png, caption)

    # The manual image becomes the comparison baseline: no duplicate alert for the same data.
    snapshot = {day: item["slots"] for day, item in sorted(schedules.items())}
    d1.query(
        "UPDATE users SET snapshot = ?, message_id = ?, last_ok = ?, fail_count = 0, "
        "error_notified = 0, no_schedule_notified = 0, updated_at = CURRENT_TIMESTAMP "
        "WHERE chat_id = ?",
        [json.dumps(snapshot, ensure_ascii=False), message_id,
         now.strftime("%d.%m.%Y %H:%M"), chat_id],
    )
    log.info("PNG за кнопкою надіслано: chat_id=%s message_id=%s", chat_id, message_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    main()
