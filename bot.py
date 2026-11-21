"""Telegram-бот графіків відключень світла (Тернопільобленерго, poweron.toe.com.ua).

Раз на 15 хв (CHECK_INTERVAL_SECONDS; можна змінити) бере графік з API сайту:
  • є зміни      → надсилає НОВЕ повідомлення з картинкою й переліком змін;
  • змін немає   → РЕДАГУЄ попереднє повідомлення (картинка + час оновлення + «Змін немає»).

Запуск:
  python bot.py              — працювати постійно
  python bot.py --once       — одна перевірка й вихід
  python bot.py --preview    — завантажити графік, вивести в консоль і зберегти preview.png (без Telegram)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from cryptography.fernet import Fernet, InvalidToken

from config import BASE_DIR, Config, load_config
from render import build_views, render_png
from telegram_api import Telegram, TelegramError
from toe_api import OFF, count_minutes, diff_snapshots, fetch_schedule, fmt_duration, fmt_range, \
    intervals_of, snapshot_of

log = logging.getLogger("bot")


# ───────────────────────────── стан між перезапусками ─────────────────────────────
@dataclass
class State:
    chat_id: str = ""
    message_id: int | None = None
    snapshot: dict | None = None          # None = ще жодного разу не зберігали
    fail_count: int = 0
    error_notified: bool = False
    last_ok: str = ""
    path: Path = field(default=Path("state.json"), repr=False)

    @classmethod
    def load(cls, path: Path) -> "State":
        st = cls(path=path)
        if path.exists():
            try:
                payload = path.read_bytes()
                key = os.environ.get("STATE_ENCRYPTION_KEY", "").strip()
                if key:
                    payload = Fernet(key.encode("ascii")).decrypt(payload)
                raw = json.loads(payload.decode("utf-8"))
                for name in ("chat_id", "message_id", "snapshot", "fail_count", "error_notified", "last_ok"):
                    if name in raw:
                        setattr(st, name, raw[name])
            except (ValueError, OSError, InvalidToken) as exc:
                raise SystemExit(
                    f"Не вдалося прочитати стан {path}. Перевірте STATE_ENCRYPTION_KEY: {exc}"
                ) from exc
        return st

    def save(self) -> None:
        data = {k: v for k, v in asdict(self).items() if k != "path"}
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        key = os.environ.get("STATE_ENCRYPTION_KEY", "").strip()
        if key:
            payload = Fernet(key.encode("ascii")).encrypt(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, self.path)


# ───────────────────────────── підписи до повідомлень ─────────────────────────────
def caption_first(group: str, now: datetime) -> str:
    return f"📊 <b>Графік відключень</b> · черга {group}\n🕒 Оновлено о {now:%H:%M}"


def caption_changed(group: str, now: datetime, lines: list[str]) -> str:
    shown = lines[:12] + (["…"] if len(lines) > 12 else [])
    body = "\n".join(shown)
    return f"🔔 <b>Графік оновлено!</b> · черга {group}\n{body}\n🕒 Оновлено о {now:%H:%M}"


def caption_nochange(now: datetime) -> str:
    return f"✅ <b>Змін немає</b>\n🕒 Оновлено о {now:%H:%M}"


# ───────────────────────────── один цикл перевірки ─────────────────────────────
def run_cycle(cfg: Config, tg: Telegram, tz: ZoneInfo, state: State, http: requests.Session) -> None:
    now = datetime.now(tz)
    interval_min = max(1, cfg.interval_seconds // 60)

    try:
        days = fetch_schedule(cfg.address_key, cfg.group, tz, http)
        snapshot = snapshot_of(days)
        if not snapshot:
            raise RuntimeError("сайт не повернув графік на сьогодні або завтра; не публікую порожнє зображення")
    except Exception as exc:  # мережа, 5xx, зміна формату тощо
        _on_fetch_failure(cfg, tg, state, now, exc)
        return

    state.fail_count, state.error_notified, state.last_ok = 0, False, f"{now:%d.%m %H:%M}"

    views = build_views(days, now.date())
    first = state.message_id is None
    changes = [] if state.snapshot is None else diff_snapshots(state.snapshot, snapshot, now.date())

    if first or changes:
        status = "first" if first else "changed"
        caption = caption_first(cfg.group, now) if first else caption_changed(cfg.group, now, changes)
        png = render_png(views, cfg.address_label, cfg.group, status, now, interval_min)
        _send_new(cfg, tg, state, png, caption)
        log.info("Надіслано нове повідомлення (%s): %s", status, "; ".join(changes) or "перший запуск")
    else:
        png = render_png(views, cfg.address_label, cfg.group, "nochange", now, interval_min)
        try:
            tg.edit_photo(state.chat_id, state.message_id, png, caption_nochange(now))
            log.info("Змін немає — повідомлення %s відредаговано", state.message_id)
        except TelegramError as exc:
            if exc.not_modified:
                log.info("Змін немає — Telegram каже, що повідомлення вже актуальне")
            elif exc.message_gone:
                log.warning("Повідомлення видалено в чаті — надсилаю нове")
                _send_new(cfg, tg, state, png, caption_nochange(now), delete_old=False)
            else:
                raise

    state.snapshot = snapshot
    state.save()


def _send_new(cfg: Config, tg: Telegram, state: State, png: bytes, caption: str,
              delete_old: bool = True) -> None:
    old_id = state.message_id
    state.message_id = tg.send_photo(state.chat_id, png, caption)
    state.save()
    if cfg.delete_previous and delete_old and old_id:
        try:
            tg.delete_message(state.chat_id, old_id)
        except TelegramError as exc:
            log.warning("Не вдалося видалити старе повідомлення %s: %s", old_id, exc)


def _on_fetch_failure(cfg: Config, tg: Telegram, state: State, now: datetime, exc: Exception) -> None:
    state.fail_count += 1
    log.error("Не вдалося отримати графік (спроба поспіль №%d): %s", state.fail_count, exc)
    if state.fail_count >= 3 and not state.error_notified and state.message_id:
        text = (f"⚠️ <b>Не вдається оновити дані з сайту обленерго</b>\n"
                f"Остання успішна перевірка: {state.last_ok or 'невідомо'}\n"
                f"Показано останній відомий графік. Бот продовжує спроби.")
        try:
            tg.edit_caption(state.chat_id, state.message_id, text)
            state.error_notified = True
        except TelegramError as tg_exc:
            log.warning("Не вдалося показати попередження: %s", tg_exc)
    state.save()


# ───────────────────────────── режим --preview ─────────────────────────────
def preview(cfg: Config) -> None:
    tz = ZoneInfo(cfg.timezone)
    now = datetime.now(tz)
    days = fetch_schedule(cfg.address_key, cfg.group, tz)
    if not days:
        print("Сайт відповів, але графіка на сьогодні/завтра для цієї адреси та черги немає.\n"
              "Перевірте TOE_ADDRESS і GROUP у .env (див. README, розділ «Адреса»).")
    for day, sched in sorted(days.items()):
        print(f"\n{day:%d.%m.%Y}  (дані від {sched.created or '—'})  черга {cfg.group}")
        rows = intervals_of(sched.slots)
        if not rows:
            print("  🟢 відключень немає")
        for code, a, b in rows:
            icon = "🔴" if code == OFF else "🟡"
            print(f"  {icon} {fmt_range(a, b)}  ({fmt_duration((b - a) * 30)})")
        print(f"  разом без світла: {fmt_duration(count_minutes(sched.slots, OFF)) or '0 хв'}")
    png = render_png(build_views(days, now.date()), cfg.address_label, cfg.group, "nochange", now,
                     max(1, cfg.interval_seconds // 60))
    out = BASE_DIR / "preview.png"
    out.write_bytes(png)
    print(f"\nКартинку збережено: {out}")


# ───────────────────────────── точка входу ─────────────────────────────
def setup_logging(log_file: str = "bot.log") -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_file:
        file_handler = RotatingFileHandler(BASE_DIR / log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="одна перевірка й вихід")
    parser.add_argument("--preview", action="store_true", help="лише завантажити графік і зберегти preview.png")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(cfg.log_file)

    if args.preview:
        preview(cfg)
        return

    tz = ZoneInfo(cfg.timezone)
    tg = Telegram(cfg.bot_token)
    state = State.load(cfg.state_file)

    chat_id = cfg.chat_id or state.chat_id
    if not chat_id:
        try:
            chat_id = tg.detect_chat_id() or ""
        except TelegramError as exc:
            raise SystemExit(f"Не вдалося перевірити токен / отримати чат: {exc}")
    if not chat_id:
        raise SystemExit("Не знайдено чат. Відкрийте свого бота в Telegram, натисніть /start і запустіть знову "
                         "(або вкажіть TELEGRAM_CHAT_ID у .env).")
    if chat_id != state.chat_id:  # інший чат → починаємо з нового повідомлення
        state.chat_id, state.message_id, state.snapshot = chat_id, None, None
        state.save()

    log.info("Старт. Адреса: %s · черга %s · інтервал %d с · чат %s",
             cfg.address_label, cfg.group, cfg.interval_seconds, state.chat_id)

    http = requests.Session()
    while True:
        started = time.monotonic()
        try:
            run_cycle(cfg, tg, tz, state, http)
        except TelegramError as exc:
            log.error("Помилка Telegram: %s", exc)
        except Exception:  # бот не має падати через разовий збій
            log.exception("Непередбачена помилка в циклі")
        if args.once:
            return
        time.sleep(max(5.0, cfg.interval_seconds - (time.monotonic() - started)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nЗупинено.")
