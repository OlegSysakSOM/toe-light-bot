"""Налаштування бота. Читає змінні з файлу .env (поруч зі скриптом) та з оточення."""
from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def parse_address_key(value: str) -> str:
    """Приймає `cityId/streetId/будинок` або base64 цього рядка (заголовок X-debug-key)."""
    value = value.strip()
    if not value:
        raise ValueError("TOE_ADDRESS порожній")
    if "/" not in value:
        try:
            padded = value + "=" * (-len(value) % 4)
            value = base64.b64decode(padded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError(
                "TOE_ADDRESS має бути у форматі cityId/streetId/будинок "
                "або base64-значенням заголовка X-debug-key"
            ) from exc
    parts = value.split("/", 2)
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ValueError(f"Невірний формат адреси: {value!r}. Очікую cityId/streetId/будинок")
    return "/".join(p.strip() for p in parts)


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_id: str
    address_key: str
    group: str
    address_label: str
    interval_seconds: int
    timezone: str
    delete_previous: bool
    state_file: Path
    log_file: str = "bot.log"


def load_config() -> Config:
    _load_dotenv(BASE_DIR / ".env")
    env = os.environ.get

    token = env("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задано TELEGRAM_BOT_TOKEN у файлі .env")

    try:
        address_key = parse_address_key(env("TOE_ADDRESS", ""))
    except ValueError as exc:
        raise SystemExit(f"Помилка в TOE_ADDRESS: {exc}") from exc

    return Config(
        bot_token=token,
        chat_id=env("TELEGRAM_CHAT_ID", "").strip(),
        address_key=address_key,
        group=env("GROUP", "5.1").strip(),
        address_label=env("ADDRESS_LABEL", "Моя адреса").strip(),
        interval_seconds=max(60, int(env("CHECK_INTERVAL_SECONDS", "900"))),
        timezone=env("TIMEZONE", "Europe/Kyiv").strip(),
        delete_previous=env("DELETE_PREVIOUS", "0").strip().lower() in {"1", "true", "yes"},
        state_file=BASE_DIR / env("STATE_FILE", "state.json"),
        log_file=env("LOG_FILE", "bot.log").strip(),
    )
