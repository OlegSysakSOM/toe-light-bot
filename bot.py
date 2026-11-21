"""Public multi-user Telegram bot for Ternopiloblenergo outage schedules.

Runs once per GitHub Actions invocation. Incoming updates are handled via
getUpdates; subscriptions and message IDs are stored in encrypted state.enc.
"""
from __future__ import annotations

import argparse
import html
import logging
import sys
from datetime import datetime
from typing import Any
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
    "Щоб підключити персональний графік, надішліть назву населеного пункту. "
    "Потім я запитаю вулицю та номер будинку, знайду вашу чергу й надсилатиму оновлення.\n\n"
    "Команди: /address — змінити адресу, /status — моя підписка, "
    "/stop — вимкнути сповіщення, /help — допомога."
)


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


def _send(tg: Telegram, chat_id: str, text: str) -> None:
    tg.send_message(chat_id, text)


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


def _activate_subscription(chat_id: str, user: dict[str, Any], candidate: dict[str, str], tg: Telegram) -> None:
    if not candidate.get("group") or normalize(candidate["group"]) in {"не входить", "не входить в графік"}:
        _send(tg, chat_id,
              "⚠️ Для цієї адреси сайт не вказав чергу погодинних відключень (ГПВ). "
              "Перевірте адресу на poweron.toe.com.ua або спробуйте іншу.")
        user["flow"] = None
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
    _send(
        tg, chat_id,
        "✅ <b>Підписку активовано!</b>\n\n"
        f"📍 {_safe_text(candidate['label'])}\n"
        f"🔢 Черга: <b>{_safe_text(candidate['group'])}</b>\n\n"
        "Під час найближчої перевірки надішлю актуальне зображення графіка. "
        "Надалі повідомлення оновлюватиметься автоматично.",
    )


def _handle_text(chat_id: str, text: str, state: dict[str, Any], tg: Telegram,
                 cfg: Any, http: requests.Session) -> None:
    users = state.setdefault("users", {})
    user = users.setdefault(chat_id, _new_user())
    command = _command(text)

    if command == "/help":
        _send(tg, chat_id,
              "<b>Допомога</b>\n"
              "/start — підключити або відновити підписку\n"
              "/address — змінити адресу\n"
              "/status — переглянути адресу й чергу\n"
              "/stop — зупинити сповіщення\n"
              "/cancel — скасувати введення адреси\n\n"
              "Бот працює в особистому чаті. Зараз підтримуються адреси з довідника "
              "Тернопільобленерго на poweron.toe.com.ua.")
        return

    if command == "/cancel":
        user["flow"] = None
        user["pending_matches"] = []
        _send(tg, chat_id, "Введення адреси скасовано. Поточна підписка (якщо є) залишилася без змін.")
        return

    if command == "/stop":
        if user.get("active"):
            user["active"] = False
            user["flow"] = None
            _send(tg, chat_id, "🔕 Сповіщення вимкнено. Щоб відновити їх, надішліть /start.")
        else:
            _send(tg, chat_id, "Активної підписки немає. Для підключення надішліть /start.")
        return

    if command == "/status":
        if user.get("address_key"):
            status = "🟢 активна" if user.get("active") else "⚪ вимкнена"
            _send(tg, chat_id,
                  "<b>Ваша підписка</b>\n"
                  f"Стан: {status}\n"
                  f"Адреса: {_safe_text(user.get('address_label', '—'))}\n"
                  f"Черга: <b>{_safe_text(user.get('group', '—'))}</b>\n"
                  f"Останнє успішне оновлення: {_safe_text(user.get('last_ok') or 'ще не було')}")
        else:
            _send(tg, chat_id, "Адресу ще не налаштовано. Надішліть /start.")
        return

    if command == "/address":
        user["flow"] = "city"
        user["city_input"] = ""
        user["street_input"] = ""
        user["pending_matches"] = []
        _send(tg, chat_id, "🏙️ Введіть назву населеного пункту (наприклад, Велика Лука).")
        return

    if command == "/start":
        if user.get("address_key"):
            if not user.get("active"):
                user["active"] = True
                user["snapshot"] = {}
                user["message_id"] = None
                _send(tg, chat_id,
                      "✅ Підписку відновлено!\n"
                      f"📍 {_safe_text(user.get('address_label', ''))}\n"
                      f"🔢 Черга: <b>{_safe_text(user.get('group', ''))}</b>\n\n"
                      "Актуальний графік надійде під час найближчої перевірки.")
            else:
                _send(tg, chat_id,
                      "Ваша підписка вже активна ✅\n"
                      f"📍 {_safe_text(user.get('address_label', ''))}\n"
                      f"🔢 Черга: <b>{_safe_text(user.get('group', ''))}</b>\n\n"
                      "Для зміни адреси: /address. Для допомоги: /help.")
            return
        user["flow"] = "city"
        user["city_input"] = ""
        user["street_input"] = ""
        user["pending_matches"] = []
        _send(tg, chat_id, WELCOME + "\n\n🏙️ <b>Крок 1/3.</b> Введіть назву населеного пункту:")
        return

    flow = user.get("flow")
    if not flow:
        _send(tg, chat_id, "Щоб підключити графік, надішліть /start. Команди: /help.")
        return

    value = text.strip()
    if not value:
        _send(tg, chat_id, "Будь ласка, надішліть текстове значення або /cancel.")
        return

    if flow == "city":
        user["city_input"] = value
        user["flow"] = "street"
        _send(tg, chat_id, "🛣️ <b>Крок 2/3.</b> Введіть назву вулиці (наприклад, Івана Франка).")
        return

    if flow == "street":
        user["street_input"] = value
        user["flow"] = "building"
        _send(tg, chat_id, "🏠 <b>Крок 3/3.</b> Введіть номер будинку (наприклад, 24).")
        return

    if flow == "building":
        _send(tg, chat_id, "🔎 Шукаю адресу в довіднику Тернопільобленерго. Перший пошук може зайняти кілька хвилин…")
        try:
            matches = _find_matches_for_user(user, value, cfg, http)
        except (ToeAPIError, ValueError) as exc:
            log.warning("Address lookup failed for chat %s: %s", chat_id, exc)
            _send(tg, chat_id,
                  "⚠️ Не вдалося завершити пошук адреси. Перевірте написання та спробуйте ще раз командою /address.\n"
                  f"Деталь: {_safe_text(str(exc)[:250])}")
            user["flow"] = "building"
            return
        if not matches:
            _send(tg, chat_id,
                  "Не знайшов таку адресу в довіднику Тернопільобленерго. "
                  "Перевірте назву населеного пункту, вулиці й номер будинку та повторіть /address.")
            user["flow"] = None
            return
        if len(matches) == 1:
            _activate_subscription(chat_id, user, matches[0], tg)
            return

        user["pending_matches"] = matches[:10]
        user["flow"] = "choose"
        lines = ["Знайшов кілька відповідних записів. Надішліть номер потрібного варіанта:"]
        for index, candidate in enumerate(user["pending_matches"], 1):
            lines.append(
                f"{index}. {_safe_text(candidate['label'])} — черга <b>{_safe_text(candidate['group'] or 'не вказана')}</b>"
            )
        _send(tg, chat_id, "\n".join(lines))
        return

    if flow == "choose":
        try:
            choice = int(value)
        except ValueError:
            choice = 0
        options = user.get("pending_matches", [])
        if not (1 <= choice <= len(options)):
            _send(tg, chat_id, f"Введіть номер від 1 до {len(options)} або /cancel.")
            return
        _activate_subscription(chat_id, user, options[choice - 1], tg)
        return

    user["flow"] = None
    _send(tg, chat_id, "Сесію введення адреси скинуто. Надішліть /address, щоб почати знову.")



def bootstrap_skip_old_updates(tg: Telegram, state: dict[str, Any], store: StateStore) -> None:
    """Discard stale pre-deployment chat history on first migration only.

    Users should send /start after the first workflow run. This avoids processing
    old test messages (for example, the old single-user bot's /start) as an address.
    """
    log.info("First run of multi-user version: clearing pre-deployment Telegram updates")
    for _ in range(100):
        updates = tg.get_updates(int(state.get("update_offset", 0)), limit=100)
        if not updates:
            break
        state["update_offset"] = int(updates[-1].get("update_id", 0)) + 1
        store.save(state)
        if len(updates) < 100:
            # Confirm the last batch and check whether any update arrived meanwhile.
            continue
    state["bootstrap_complete"] = True
    store.save(state)
    log.info("Bootstrap complete. Send /start to the bot to register your address.")


def process_updates(tg: Telegram, state: dict[str, Any], cfg: Any, store: StateStore,
                    http: requests.Session) -> None:
    updates = tg.get_updates(int(state.get("update_offset", 0)))
    if updates:
        log.info("Отримано Telegram-оновлень: %d", len(updates))
    for update in updates:
        update_id = int(update.get("update_id", 0))
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        try:
            if chat_id and chat.get("type") == "private":
                text = message.get("text")
                if isinstance(text, str):
                    _handle_text(chat_id, text, state, tg, cfg, http)
            # Only confirm an update after it was processed without an exception.
            state["update_offset"] = update_id + 1
            store.save(state)
        except Exception:
            log.exception("Не вдалося обробити Telegram update_id=%s", update_id)
            # Keep this and subsequent updates unconfirmed so they can be retried.
            break


def _changed(old: dict[str, str], new: dict[str, str]) -> bool:
    if not old:
        return True
    for day, slots in new.items():
        if old.get(day) != slots:
            return True
    return False


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
                          tz: ZoneInfo, http: requests.Session, store: StateStore) -> None:
    users = state.setdefault("users", {})
    active = [(chat_id, user) for chat_id, user in users.items()
              if user.get("active") and user.get("address_key") and user.get("group")]
    if not active:
        log.info("Немає активних підписок")
        return

    now = datetime.now(tz)
    schedule_cache: dict[str, dict[str, dict[str, str]] | Exception] = {}
    for chat_id, user in active:
        identity = f"{user['address_key']}|{user['group']}"
        if identity not in schedule_cache:
            try:
                schedules = fetch_schedule(user["address_key"], user["group"], tz, session=http)
                if not schedules:
                    raise ToeAPIError("Сайт поки не повернув графік на сьогодні або завтра")
                schedule_cache[identity] = schedules
            except Exception as exc:
                schedule_cache[identity] = exc

        result = schedule_cache[identity]
        if isinstance(result, Exception):
            user["fail_count"] = int(user.get("fail_count", 0)) + 1
            log.warning("Schedule fetch failed for subscription %s: %s", identity, result)
            if user["fail_count"] >= 3 and not user.get("error_notified"):
                try:
                    if user.get("snapshot"):
                        warning = "Показую останній отриманий графік і продовжую спроби. "
                    else:
                        warning = "Поки не вдалося отримати перший графік; продовжую спроби. "
                    _send(tg, chat_id,
                          "⚠️ <b>Тимчасово не вдається оновити графік.</b>\n"
                          + warning + "Це не означає, що відключень немає.")
                    user["error_notified"] = True
                except TelegramError:
                    log.exception("Не вдалося надіслати повідомлення про збій")
            store.save(state)
            continue

        snapshot = {day: item["slots"] for day, item in sorted(result.items())}
        old_snapshot = user.get("snapshot") or {}
        changed = _changed(old_snapshot, snapshot)
        if not old_snapshot:
            status = "✅ Перший актуальний графік для цієї підписки."
        elif changed:
            status = "🔔 <b>ГРАФІК ЗМІНИВСЯ!</b> Надсилаю оновлення."
        else:
            status = "✅ Змін немає — графік перевірено."
        caption = _caption(user, now, status)
        png = render_schedule_png(result, user["address_label"], user["group"], now)

        try:
            message_id = user.get("message_id")
            if message_id and not changed:
                try:
                    tg.edit_photo(chat_id, int(message_id), png, caption)
                except TelegramError as exc:
                    if not exc.not_modified and not exc.message_gone:
                        log.warning("Не вдалося редагувати фото в чаті %s; надсилаю нове: %s", chat_id, exc)
                        user["message_id"] = tg.send_photo(chat_id, png, caption)
                    elif exc.message_gone:
                        user["message_id"] = tg.send_photo(chat_id, png, caption)
            else:
                user["message_id"] = tg.send_photo(chat_id, png, caption)

            user["snapshot"] = snapshot
            user["last_ok"] = now.strftime("%d.%m.%Y %H:%M")
            user["fail_count"] = 0
            if user.get("error_notified"):
                _send(tg, chat_id, "✅ Зв'язок із сайтом відновлено, графік знову оновлюється.")
            user["error_notified"] = False
            user["no_schedule_notified"] = False
        except Exception:
            log.exception("Не вдалося оновити повідомлення підписки для чату %s", chat_id)
        store.save(state)


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

    cfg = load_config()
    setup_logging(cfg.log_level)
    store = StateStore(cfg.state_file, cfg.state_encryption_key)
    state = store.load()
    tz = ZoneInfo(cfg.timezone)
    tg = Telegram(cfg.bot_token)
    http = requests.Session()
    http.headers.update({"User-Agent": "TOE-Outage-Bot/2.0"})

    # Ensure long polling is usable; do not discard users' pending messages.
    tg.delete_webhook()
    if not state.get("bootstrap_complete", False):
        bootstrap_skip_old_updates(tg, state, store)
    else:
        process_updates(tg, state, cfg, store, http)
    process_subscriptions(tg, state, cfg, tz, http, store)
    store.save(state)
    log.info("Cycle complete. Active subscriptions: %d", sum(1 for u in state["users"].values() if u.get("active")))


if __name__ == "__main__":
    main()
