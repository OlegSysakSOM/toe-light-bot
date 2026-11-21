"""Configuration for the public multi-user TOE outage bot."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def load_dotenv(path: Path | None = None) -> None:
    """Tiny .env loader; environment variables always take precedence."""
    target = path or BASE_DIR / ".env"
    if not target.exists():
        return
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    bot_token: str
    state_encryption_key: str
    state_file: Path
    address_index_file: Path
    timezone: str = "Europe/Kyiv"
    max_address_pages: int = 600
    schedule_check_interval_seconds: int = 900
    log_level: str = "INFO"


def load_config() -> Config:
    load_dotenv()
    env = os.environ.get
    token = env("TELEGRAM_BOT_TOKEN", "").strip()
    key = env("STATE_ENCRYPTION_KEY", "").strip()
    if not token:
        raise SystemExit("Не задано TELEGRAM_BOT_TOKEN (GitHub Secret або .env).")
    if not key:
        raise SystemExit("Не задано STATE_ENCRYPTION_KEY (GitHub Secret або .env).")
    return Config(
        bot_token=token,
        state_encryption_key=key,
        state_file=BASE_DIR / env("STATE_FILE", "state.enc"),
        address_index_file=BASE_DIR / env("ADDRESS_INDEX_FILE", ".cache/toe_address_index.json"),
        timezone=env("TIMEZONE", "Europe/Kyiv").strip(),
        max_address_pages=max(1, int(env("MAX_ADDRESS_PAGES", "600"))),
        schedule_check_interval_seconds=max(300, int(env("SCHEDULE_CHECK_INTERVAL_SECONDS", "900"))),
        log_level=env("LOG_LEVEL", "INFO").upper(),
    )
