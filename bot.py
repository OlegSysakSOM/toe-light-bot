"""Public multi-user Telegram bot for Ternopiloblenergo outage schedules.

Runs once per GitHub Actions invocation. Incoming updates are handled via
getUpdates; subscriptions and message IDs are stored in encrypted state.enc.
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from config import load_config
from render import render_schedule_png
from state_store import StateStore
from telegram_api import Telegram, TelegramError
from toe_api import ToeAPIError, find_address_matches, fetch_schedule, load_or_build_address_index, normalize

log = logging.getLogger("public_toe_bot")

WELCOME = (
    "👋 <b>Вітаю! Це публічний бот графіків відключень Тернопільобленерго.</b>\n\n"
    "Керувати підпискою можна кнопками внизу екрана: додати або змінити адресу, "
    "переглянути графік чи вимкнути сповіщення."
)

BTN_ADD = "➕ Додати адресу"
BTN_CHANGE = "✏️ Змінити адресу"
BTN_STATUS = "📊 Мій графік"
BTN_STOP = "🔕 Вимкнути сповіщення"
BTN_RESUME = "🔔 Увімкнути сповіщення"
BTN_HELP = "ℹ️ Допомога"
BTN_CANCEL = "❌ Скасувати"


def _reply_keyboard(user: dict[str, Any] | None = None) -> dict[str, Any]:
    """Always show the full menu, even while the user is entering an address.

    The previous version hid every menu action except Cancel/Help whenever a
    conversation flow was active. That looked like the bot only had two buttons
    and made it hard to recover from a stale address-entry state.
    """
    user = user or {}
    notification_button = BTN_STOP if user.get("active") else BTN_RESUME
    return {
        "keyboard": [
            [{"text": BTN_ADD}, {"text": BTN_CHANGE}],
            [{"text": BTN_STATUS}, {"text": notification_button}],
            [{"text": BTN_CANCEL}, {"text": BTN_HELP}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": (
            "Введіть населений пункт" if user.get("flow") == "city" else
            "Введіть назву вулиці" if user.get("flow") == "street" else
            "Введіть номер будинку" if user.get("flow") == "building" else
            "Оберіть дію в меню"
        ),
    }


def _new_user() -> dict[str, Any]:
    return {
        "active": False,
        "flow": None,
        "city_input": "",
        "street_input": "",
        "pending_matches": [],
        "address_key": "",
        "address_label": "",
        "group": "",
        "snapshot": {},
        "message_id": None,
        "fail_count": 0,
        "error_notified": False,
        "no_schedule_notified": False,
        "last_ok": "",
    }


def _safe_text(value: str) -> str:
    return html.escape(str(value), quote=False)


def _command(text: str) -> str | None:
    if not text.startswith("/"):
        return None
    return text.split()[0].split("@", 1)[0].lower()


def _send(tg: Telegram, chat_id: str, text: str, *,
          user: dict[str, Any] | None = None,
          reply_markup: dict[str, Any] | None = None) -> None:
    tg.send_message(
        chat_id,
        text,
        reply_markup=reply_markup if reply_markup is not None else _reply_keyboard(user),
    )


def _find_matches_for_user(user: dict[str, Any], building: str,
                           cfg: Any, http: requests.Session) -> list[dict[str, str]]:
    # Full public address directory is restored via actions/cache. It is scanned
    # from TOE only once per cache version; subsequent users are resolved locally.
    address_rows = load_or_build_address_index(
        str(cfg.address_index_file),
        session=http,
        max_pages=cfg.max_address_pages,
    )
    return find_address_matches(
        user["city_input"], user["street_input"], building,
        session=http,
        max_pages=cfg.max_address_pages,
        cached_records=address_rows,
    )


def _activate_subscription(chat_id: str, user: dict[str, Any], candidate: dict[str, str],
                           tg: Telegram, state: dict[str, Any]) -> None:
    if not candidate.get("group") or normalize(candidate["group"]) in {"не входить", "не входить в графік"}:
        user["flow"] = None
        _send(
            tg, chat_id,
            "⚠️ Для цієї адреси сайт не вказав чергу погодинних відключень (ГПВ). "
            "Перевірте адресу на poweron.toe.com.ua або спробуйте іншу.",
            user=user,
        )
        return

    user.update({
        "active": True,
        "flow": None,
        "pending_matches": [],
        "address_key": candidate["address_key"],
        "address_label": candidate["label"],
        "group": candidate["group"],
        "snapshot": {},
        "message_id": None,
        "fail_count": 0,
        "error_notified": False,
        "no_schedule_notified": False,
        "last_ok": "",
    })
    state["force_schedule_check"] = True
    _send(
        tg, chat_id,
        "✅ <b>Підписку активовано!</b>\n\n"
        f"📍 {_safe_text(candidate['label'])}\n"
        f"🔢 Черга: <b>{_safe_text(candidate['group'])}</b>\n\n"
        "Найближчим циклом надішлю актуальне зображення графіка. "
        "Надалі графік перевірятиметься приблизно кожні 10 хвилин.",
        user=user,
    )



def _begin_address_flow(chat_id: str, user: dict[str, Any], tg: Telegram, *, change: bool = False) -> None:
    user["flow"] = "city"
    user["city_input"] = ""
    user["street_input"] = ""
    user["pending_matches"] = []
    intro = (
        "Змінимо вашу адресу. Поточна підписка залишатиметься активною, "
        "доки нову адресу не буде підтверджено."
        if change else "Підключимо вашу адресу."
    )
    _send(
        tg, chat_id,
        f"🏙️ <b>{intro}</b>\n\nКрок 1/3. Введіть назву населеного пункту (наприклад, Велика Лука).",
        user=user,
    )


def _handle_text(chat_id: str, text: str, state: dict[str, Any], tg: Telegram,
                 cfg: Any, http: requests.Session) -> None:
    users = state.setdefault("users", {})
    user = users.setdefault(chat_id, _new_user())
    value = text.strip()

    # Persistent reply-keyboard buttons are the primary interface.
    if value == BTN_ADD:
        _begin_address_flow(chat_id, user, tg, change=False)
        return
    if value == BTN_CHANGE:
        _begin_address_flow(chat_id, user, tg, change=bool(user.get("address_key")))
        return
    if value == BTN_STATUS:
        value = "/status"
    elif value == BTN_STOP:
        value = "/stop"
    elif value == BTN_RESUME:
        value = "/resume"
    elif value == BTN_HELP:
        value = "/help"
    elif value == BTN_CANCEL:
        value = "/cancel"

    command = _command(value)

    if command == "/help":
        _send(
            tg, chat_id,
            "<b>Як користуватися ботом</b>\n"
            "• Натисніть «➕ Додати адресу», щоб підключити графік.\n"
            "• Натисніть «✏️ Змінити адресу», щоб вибрати іншу адресу.\n"
            "• Натисніть «📊 Мій графік», щоб переглянути підписку.\n"
            "• Кнопками «Вимкнути/Увімкнути сповіщення» керуйте повідомленнями.\n\n"
            "Бот працює в особистому чаті та підтримує адреси з довідника poweron.toe.com.ua.",
            user=user,
        )
        return

    if command == "/cancel":
        user["flow"] = None
        user["pending_matches"] = []
        _send(tg, chat_id, "Введення адреси скасовано. Поточна підписка залишилася без змін.", user=user)
        return

    if command == "/stop":
        if user.get("active"):
            user["active"] = False
            user["flow"] = None
            _send(tg, chat_id, "🔕 Сповіщення вимкнено.", user=user)
        else:
            _send(tg, chat_id, "Активної підписки немає.", user=user)
        return

    if command == "/resume":
        if user.get("address_key"):
            if not user.get("active"):
                user["active"] = True
                user["snapshot"] = {}
                user["message_id"] = None
                state["force_schedule_check"] = True
                _send(tg, chat_id, "✅ Сповіщення знову увімкнено. Графік надійде під час найближчого циклу.", user=user)
            else:
                _send(tg, chat_id, "Сповіщення вже увімкнені ✅", user=user)
        else:
            _send(tg, chat_id, "Спочатку підключіть адресу кнопкою «➕ Додати адресу».", user=user)
        return

    if command == "/status":
        if user.get("address_key"):
            status = "🟢 активна" if user.get("active") else "⚪ вимкнена"
            _send(
                tg, chat_id,
                "<b>Ваша підписка</b>\n"
                f"Стан: {status}\n"
                f"Адреса: {_safe_text(user.get('address_label', '—'))}\n"
                f"Черга: <b>{_safe_text(user.get('group', '—'))}</b>\n"
                f"Останнє успішне оновлення: {_safe_text(user.get('last_ok') or 'ще не було')}",
                user=user,
            )
        else:
            _send(tg, chat_id, "Адресу ще не налаштовано. Натисніть «➕ Додати адресу».", user=user)
        return

    if command == "/address":
        _begin_address_flow(chat_id, user, tg, change=bool(user.get("address_key")))
        return

    if command == "/start":
        if user.get("address_key"):
            if not user.get("active"):
                user["active"] = True
                user["snapshot"] = {}
                user["message_id"] = None
                state["force_schedule_check"] = True
                _send(
                    tg, chat_id,
                    "✅ Підписку відновлено!\n"
                    f"📍 {_safe_text(user.get('address_label', ''))}\n"
                    f"🔢 Черга: <b>{_safe_text(user.get('group', ''))}</b>\n\n"
                    "Графік надійде під час найближчого циклу.",
                    user=user,
                )
            else:
                _send(
                    tg, chat_id,
                    WELCOME + "\n\nВаша підписка вже активна ✅\n"
                    f"📍 {_safe_text(user.get('address_label', ''))}\n"
                    f"🔢 Черга: <b>{_safe_text(user.get('group', ''))}</b>",
                    user=user,
                )
            return
        _send(tg, chat_id, WELCOME, user=user)
        return

    flow = user.get("flow")
    if not flow:
        _send(tg, chat_id, "Скористайтеся кнопками внизу екрана, щоб керувати підпискою.", user=user)
        return

    if not value:
        _send(tg, chat_id, "Будь ласка, введіть текстове значення або натисніть «❌ Скасувати».", user=user)
        return

    if flow == "city":
        user["city_input"] = value
        user["flow"] = "street"
        _send(tg, chat_id, "🛣️ <b>Крок 2/3.</b> Введіть назву вулиці (наприклад, Івана Франка).", user=user)
        return

    if flow == "street":
        user["street_input"] = value
        user["flow"] = "building"
        _send(tg, chat_id, "🏠 <b>Крок 3/3.</b> Введіть номер будинку (наприклад, 24).", user=user)
        return

    if flow == "building":
        _send(tg, chat_id, "🔎 Шукаю адресу в довіднику Тернопільобленерго. Перший пошук може зайняти кілька хвилин…", user=user)
        try:
            matches = _find_matches_for_user(user, value, cfg, http)
        except (ToeAPIError, ValueError) as exc:
            log.warning("Address lookup failed for chat %s: %s", chat_id, exc)
            _send(
                tg, chat_id,
                "⚠️ Не вдалося завершити пошук адреси. Перевірте написання та спробуйте ще раз через кнопку «Змінити адресу».\n"
                f"Деталь: {_safe_text(str(exc)[:250])}",
                user=user,
            )
            user["flow"] = "building"
            return
        if not matches:
            user["flow"] = None
            _send(
                tg, chat_id,
                "Не знайшов таку адресу в довіднику Тернопільобленерго. "
                "Перевірте населений пункт, вулицю й номер будинку та спробуйте ще раз кнопкою додавання/зміни адреси.",
                user=user,
            )
            return
        if len(matches) == 1:
            _activate_subscription(chat_id, user, matches[0], tg, state)
            return

        user["pending_matches"] = matches[:10]
        user["flow"] = "choose"
        inline_rows = []
        for index, candidate in enumerate(user["pending_matches"]):
            label = f"{index + 1}. {candidate['label']} · черга {candidate.get('group') or '—'}"
            inline_rows.append([{"text": label[:64], "callback_data": f"choose_address:{index}"}])
        _send(
            tg, chat_id,
            "Знайшов кілька відповідних адрес. Натисніть правильний варіант:",
            user=user,
            reply_markup={"inline_keyboard": inline_rows},
        )
        return

    if flow == "choose":
        try:
            choice = int(value) - 1
        except ValueError:
            choice = -1
        options = user.get("pending_matches", [])
        if not (0 <= choice < len(options)):
            _send(tg, chat_id, "Оберіть один із варіантів кнопкою на попередньому повідомленні.", user=user)
            return
        _activate_subscription(chat_id, user, options[choice], tg, state)
        return

    user["flow"] = None
    _send(tg, chat_id, "Сесію введення адреси скинуто. Натисніть «Змінити адресу», щоб почати знову.", user=user)


def _handle_callback(callback: dict[str, Any], state: dict[str, Any], tg: Telegram) -> None:
    callback_id = str(callback.get("id", ""))
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    data = str(callback.get("data", ""))
    user = state.setdefault("users", {}).get(chat_id)
    if not callback_id:
        return
    if not chat_id or chat.get("type") != "private":
        try:
            tg.answer_callback_query(callback_id, "Використовуйте бота в особистому чаті.")
        except TelegramError:
            log.debug("Callback answer expired")
        return
    if not user:
        try:
            tg.answer_callback_query(callback_id, "Спочатку натисніть Start.")
        except TelegramError:
            log.debug("Callback answer expired")
        return
    if data.startswith("choose_address:"):
        try:
            index = int(data.split(":", 1)[1])
        except ValueError:
            index = -1
        options = user.get("pending_matches", [])
        if user.get("flow") != "choose" or not (0 <= index < len(options)):
            try:
                tg.answer_callback_query(callback_id, "Цей вибір уже неактуальний. Почніть зміну адреси.")
            except TelegramError:
                log.debug("Callback answer expired")
            return
        try:
            tg.answer_callback_query(callback_id, "Адресу вибрано.")
        except TelegramError:
            log.debug("Callback answer expired")
        _activate_subscription(chat_id, user, options[index], tg, state)
        return
    try:
        tg.answer_callback_query(callback_id, "Невідома дія.")
    except TelegramError:
        log.debug("Callback answer expired")


def process_updates(tg: Telegram, state: dict[str, Any], cfg: Any, store: StateStore,
                    http: requests.Session) -> None:
    """Drain pending Telegram messages and callbacks on every workflow invocation.

    Do not discard pending updates during migration: a first /start or menu tap
    must be handled even if it arrived before the first run of this version.
    """
    offset = int(state.get("update_offset", 0))
    batches = 0
    while batches < 10:
        updates = tg.get_updates(offset)
        if not updates:
            break
        batches += 1
        log.info("Отримано Telegram-оновлень у пакеті %d: %d", batches, len(updates))
        for update in updates:
            update_id = int(update.get("update_id", 0))
            try:
                if update.get("callback_query"):
                    _handle_callback(update["callback_query"], state, tg)
                else:
                    message = update.get("message") or {}
                    chat = message.get("chat") or {}
                    chat_id = str(chat.get("id", ""))
                    text = message.get("text")
                    if chat_id and chat.get("type") == "private" and isinstance(text, str):
                        _handle_text(chat_id, text, state, tg, cfg, http)
                offset = update_id + 1
                state["update_offset"] = offset
                store.save(state)
            except Exception:
                log.exception("Не вдалося обробити Telegram update_id=%s", update_id)
                return
        if len(updates) < 100:
            break


def _changed(old: dict[str, str], new: dict[str, str]) -> bool:
    if not old:
        return True
    for day, slots in new.items():
        if old.get(day) != slots:
            return True
    return False


def schedule_snapshot_changed(previous: dict[str, str], current: dict[str, str]) -> bool:
    """Return True only for a non-empty previous snapshot that differs from live slots."""
    return bool(previous) and current != previous


def send_schedule_change_photo(tg: Telegram, chat_id: str, png: bytes, caption: str) -> int:
    """Shared delivery path for real change alerts and the isolated notification test."""
    return tg.send_photo(str(chat_id), png, caption)


def _caption(user: dict[str, Any], checked_at: datetime, status: str) -> str:
    return (
        "⚡ <b>ГРАФІК ВІДКЛЮЧЕНЬ</b>\n"
        f"📍 {_safe_text(user.get('address_label', ''))}\n"
        f"🔢 Черга: <b>{_safe_text(user.get('group', ''))}</b>\n"
        f"🕒 Оновлено: <b>{checked_at.strftime('%d.%m.%Y %H:%M')}</b> (Київ)\n\n"
        f"{status}\n"
        "Джерело: poweron.toe.com.ua"
    )


def process_subscriptions(tg: Telegram, state: dict[str, Any], cfg: Any,
                          tz: ZoneInfo, http: requests.Session, store: StateStore,
                          image_cache: Callable[[str, str, bytes, datetime], None] | None = None) -> dict[str, int]:
    """Send a new PNG only when the actual outage slots change."""
    users = state.setdefault("users", {})
    active = [(chat_id, user) for chat_id, user in users.items()
              if user.get("active") and user.get("address_key") and user.get("group")]
    stats = {"active": len(active), "fetched": 0, "baselined": 0, "unchanged": 0,
             "changed": 0, "sent": 0, "fetch_failed": 0, "send_failed": 0}
    if not active:
        log.info("Немає активних підписок")
        return stats

    now = datetime.now(tz)
    cache: dict[str, dict[str, dict[str, str]] | Exception] = {}
    cached_identities: set[str] = set()
    for chat_id, user in active:
        identity = f"{user['address_key']}|{user['group']}"
        if identity not in cache:
            try:
                schedules = fetch_schedule(user["address_key"], user["group"], tz, session=http)
                if not schedules:
                    raise ToeAPIError("Сайт поки не повернув графік на сьогодні або завтра")
                cache[identity] = schedules
            except Exception as exc:
                cache[identity] = exc

        result = cache[identity]
        if isinstance(result, Exception):
            stats["fetch_failed"] += 1
            user["fail_count"] = int(user.get("fail_count", 0)) + 1
            log.error("Не вдалося перевірити графік %s: %s", identity, result)
            store.save(state)
            continue

        stats["fetched"] += 1
        snapshot = {day: item["slots"] for day, item in sorted(result.items())}
        previous = user.get("snapshot") or {}

        # Refresh the shared R2 image cache on every successful check, even when the
        # schedule has not changed. This lets the Telegram button return the latest
        # rendered PNG immediately without waiting for a new GitHub runner.
        cached_png: bytes | None = None
        if image_cache is not None and identity not in cached_identities:
            cached_identities.add(identity)
            try:
                cached_png = render_schedule_png(result, user["address_label"], user["group"], now)
                image_cache(user["address_key"], user["group"], cached_png, now)
                log.info("PNG-кеш оновлено: %s", identity)
            except Exception:
                cached_png = None
                log.exception("Не вдалося оновити PNG-кеш: %s", identity)

        # First successful fetch is a silent baseline: the first picture is requested by the user.
        if not previous:
            user["snapshot"] = snapshot
            user["last_ok"] = now.strftime("%d.%m.%Y %H:%M")
            user["fail_count"] = 0
            stats["baselined"] += 1
            log.info("Початковий знімок створено без повідомлення: %s", identity)
            store.save(state)
            continue

        # Full comparison ignores source timestamps and checks all dates/48 half-hour slots.
        if not schedule_snapshot_changed(previous, snapshot):
            stats["unchanged"] += 1
            log.info("Графік не змінився — нічого не надсилаю: %s", identity)
            continue

        stats["changed"] += 1
        caption = _caption(user, now, "🔔 <b>ГРАФІК ЗМІНИВСЯ!</b> Надсилаю нове зображення.")
        png = cached_png or render_schedule_png(result, user["address_label"], user["group"], now)
        try:
            # Do not edit previous messages. A real change always makes a new photo message.
            message_id = send_schedule_change_photo(tg, chat_id, png, caption)
            user["message_id"] = message_id
            user["snapshot"] = snapshot
            user["last_ok"] = now.strftime("%d.%m.%Y %H:%M")
            user["fail_count"] = 0
            user["error_notified"] = False
            user["no_schedule_notified"] = False
            stats["sent"] += 1
            log.info("Нове PNG після зміни графіка: %s, message_id=%s", identity, message_id)
        except Exception:
            stats["send_failed"] += 1
            log.exception("Не вдалося надіслати PNG після зміни графіка: %s", identity)
            # Keep the old snapshot so the next cycle retries this change.
        store.save(state)

    log.info("Фоновий цикл: активних=%d, перевірено=%d, baseline=%d, без змін=%d, змінено=%d, "
             "нових PNG=%d, помилок TOE=%d, помилок Telegram=%d",
             stats["active"], stats["fetched"], stats["baselined"], stats["unchanged"],
             stats["changed"], stats["sent"], stats["fetch_failed"], stats["send_failed"])
    return stats


def schedule_check_due(state: dict[str, Any], now_ts: float, interval_seconds: int) -> bool:
    """Return True when outage data should be refreshed (separately from inbox polling)."""
    if state.get("force_schedule_check"):
        return True
    last = float(state.get("last_schedule_check_at", 0) or 0)
    return now_ts - last >= interval_seconds


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Public multi-user TOE outage schedule bot")
    parser.add_argument("--once", action="store_true", help="one processing cycle (default)")
    args = parser.parse_args()
    if os.environ.get("ENABLE_LEGACY_GETUPDATES", "0").strip() != "1":
        raise SystemExit(
            "Старий режим getUpdates вимкнено у production. "
            "Для перевірки графіків використовуйте python schedule_job.py; "
            "Telegram-кнопки обробляє Cloudflare Worker webhook."
        )

    cfg = load_config()
    setup_logging(cfg.log_level)
    store = StateStore(cfg.state_file, cfg.state_encryption_key)
    state = store.load()
    tz = ZoneInfo(cfg.timezone)
    tg = Telegram(cfg.bot_token)
    http = requests.Session()
    http.headers.update({"User-Agent": "TOE-Outage-Bot/2.0"})

    # Legacy polling is opt-in only. In production the Worker owns the webhook.
    process_updates(tg, state, cfg, store, http)

    now_ts = time.time()
    interval = int(getattr(cfg, "schedule_check_interval_seconds", 600))
    forced = bool(state.get("force_schedule_check", False))
    has_active_users = any(
        user.get("active") and user.get("address_key") and user.get("group")
        for user in state.get("users", {}).values()
    )
    if not has_active_users:
        log.info("Активних підписок немає — перевірку графіків пропускаю.")
        state["force_schedule_check"] = False
    elif schedule_check_due(state, now_ts, interval):
        log.info("Запускаю перевірку графіків (примусовий запуск: %s)", forced)
        process_subscriptions(tg, state, cfg, tz, http, store)
        state["last_schedule_check_at"] = now_ts
        state["force_schedule_check"] = False
    else:
        last_check = float(state.get("last_schedule_check_at", 0) or 0)
        remaining = max(0, int(interval - (now_ts - last_check)))
        log.info("Перевірка графіків ще не потрібна; залишилося приблизно %d с", remaining)

    state["bootstrap_complete"] = True
    store.save(state)
    log.info("Cycle complete. Active subscriptions: %d", sum(1 for u in state["users"].values() if u.get("active")))


if __name__ == "__main__":
    main()
