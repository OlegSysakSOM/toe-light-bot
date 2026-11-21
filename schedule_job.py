"""Scheduled outage refresh job. Telegram interaction is handled by the Cloudflare webhook."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import requests

from bot import process_subscriptions
from cloudflare_d1 import D1Client
from telegram_api import Telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("schedule_job")


class NoopStore:
    def save(self, _state: dict) -> None:
        return


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задано TELEGRAM_BOT_TOKEN у GitHub Secrets")
    tz = ZoneInfo(os.environ.get("TIMEZONE", "Europe/Kyiv"))
    d1 = D1Client()
    rows = d1.query(
        "SELECT chat_id, active, flow, city_input, street_input, pending_matches, "
        "address_key, address_label, group_name, snapshot, message_id, fail_count, "
        "error_notified, no_schedule_notified, last_ok "
        "FROM users WHERE active = 1 AND address_key <> '' AND group_name <> ''"
    )
    if not rows:
        log.info("Активних підписок немає.")
        return

    users: dict[str, dict] = {}
    original: dict[str, dict] = {}
    for row in rows:
        try:
            snapshot = json.loads(row.get("snapshot") or "{}")
        except (TypeError, ValueError):
            snapshot = {}
        try:
            pending = json.loads(row.get("pending_matches") or "[]")
        except (TypeError, ValueError):
            pending = []
        user_record = {
            "active": bool(row.get("active")),
            "flow": row.get("flow"),
            "city_input": row.get("city_input") or "",
            "street_input": row.get("street_input") or "",
            "pending_matches": pending,
            "address_key": row.get("address_key") or "",
            "address_label": row.get("address_label") or "",
            "group": row.get("group_name") or "",
            "snapshot": snapshot,
            "message_id": row.get("message_id"),
            "fail_count": int(row.get("fail_count") or 0),
            "error_notified": bool(row.get("error_notified")),
            "no_schedule_notified": bool(row.get("no_schedule_notified")),
            "last_ok": row.get("last_ok") or "",
        }
        users[str(row["chat_id"])] = user_record
        original[str(row["chat_id"])] = dict(user_record)

    log.info("Перевіряю графіки для активних чатів: %d", len(users))
    state = {"users": users}
    tg = Telegram(token)
    http = requests.Session()
    http.headers.update({"User-Agent": "TOE-Public-Outage-Bot/2.2"})
    cfg = SimpleNamespace(timezone=str(tz), schedule_check_interval_seconds=600)
    process_subscriptions(tg, state, cfg, tz, http, NoopStore())

    updates = []
    for chat_id, user in users.items():
        before = original[chat_id]
        # No-change image edits update the caption's timestamp but do not need
        # a D1 row write. Persist only subscription state transitions/data changes.
        persisted_fields = ("snapshot", "message_id", "fail_count", "error_notified", "no_schedule_notified")
        if all(user.get(field) == before.get(field) for field in persisted_fields):
            continue
        updates.append({
            "sql": "UPDATE users SET snapshot = ?, message_id = ?, fail_count = ?, "
                   "error_notified = ?, no_schedule_notified = ?, last_ok = ?, "
                   "updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
            "params": [
                json.dumps(user.get("snapshot") or {}, ensure_ascii=False),
                user.get("message_id"),
                int(user.get("fail_count") or 0),
                1 if user.get("error_notified") else 0,
                1 if user.get("no_schedule_notified") else 0,
                user.get("last_ok") or "",
                chat_id,
            ],
        })
    for i in range(0, len(updates), 75):
        d1.batch(updates[i:i + 75])
    checked_at = datetime.now(tz).strftime("%d.%m.%Y %H:%M:%S")
    d1.query(
        "INSERT INTO bot_meta(key, value) VALUES ('last_schedule_check_at', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [checked_at],
    )
    log.info("Цикл перевірки завершено: %d активних підписок; змінених записів у D1: %d; %s", len(users), len(updates), checked_at)


if __name__ == "__main__":
    main()
