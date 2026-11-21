"""Encrypted persistent state. Only the Fernet-encrypted state.enc is committed."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("state")


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "update_offset": 0,
        "users": {},
        "address_cache": {},
        "bootstrap_complete": False,
    }


class StateStore:
    def __init__(self, path: Path, encryption_key: str):
        self.path = path
        try:
            self.fernet = Fernet(encryption_key.encode("ascii"))
        except Exception as exc:
            raise SystemExit("STATE_ENCRYPTION_KEY має бути Fernet-ключем, згенерованим cryptography.") from exc

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_state()
        payload = self.path.read_bytes()
        try:
            clear = self.fernet.decrypt(payload)
        except InvalidToken:
            # Support local legacy JSON files, but never silently reset encrypted data
            # when the wrong key was supplied.
            if payload.lstrip().startswith(b"{"):
                clear = payload
            else:
                raise SystemExit(
                    "Не вдалося розшифрувати state.enc. Переконайтеся, що STATE_ENCRYPTION_KEY "+
                    "точно такий самий, як під час попередніх запусків. Я не перезаписав файл."
                )
        try:
            data = json.loads(clear.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SystemExit(f"Файл стану пошкоджений: {exc}") from exc

        if isinstance(data, dict) and data.get("schema_version") == 2:
            data.setdefault("users", {})
            data.setdefault("address_cache", {})
            data.setdefault("update_offset", 0)
            data.setdefault("bootstrap_complete", True)
            return data

        # Old single-user bot state has another shape. Start a clean multi-user
        # schema; the user re-subscribes using /start -> address prompts.
        log.warning("Знайдений старий формат стану одного користувача; створюю нову базу підписок.")
        return empty_state()

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encrypted = self.fernet.encrypt(raw)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(encrypted)
        os.replace(tmp, self.path)
