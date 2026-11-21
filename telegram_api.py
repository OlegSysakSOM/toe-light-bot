"""Мінімальна обгортка над Telegram Bot API (лише requests, без зайвих залежностей)."""
from __future__ import annotations

import json
import logging
import time

import requests

log = logging.getLogger("tg")


class TelegramError(Exception):
    def __init__(self, code: int | None, description: str):
        super().__init__(f"Telegram {code}: {description}")
        self.code = code
        self.description = description or ""

    @property
    def not_modified(self) -> bool:
        return "message is not modified" in self.description

    @property
    def message_gone(self) -> bool:
        d = self.description.lower()
        return any(x in d for x in ("message to edit not found", "message_id_invalid",
                                    "message can't be edited", "message to delete not found"))


class Telegram:
    def __init__(self, token: str):
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = requests.Session()

    def _call(self, method: str, data: dict | None = None, files: dict | None = None,
              timeout: int = 40, _retry: bool = True):
        try:
            resp = self._http.post(f"{self._base}/{method}", data=data, files=files, timeout=timeout)
        except requests.RequestException as exc:
            raise TelegramError(None, f"мережева помилка: {exc}") from exc
        try:
            body = resp.json()
        except ValueError:
            raise TelegramError(resp.status_code, resp.text[:200])
        if body.get("ok"):
            return body["result"]
        code, desc = body.get("error_code"), body.get("description", "")
        retry_after = (body.get("parameters") or {}).get("retry_after")
        if code == 429 and retry_after and _retry:
            log.warning("Telegram просить зачекати %s с", retry_after)
            time.sleep(int(retry_after) + 1)
            return self._call(method, data, files, timeout, _retry=False)
        raise TelegramError(code, desc)

    # ── повідомлення ──
    def send_photo(self, chat_id: str, png: bytes, caption: str) -> int:
        res = self._call("sendPhoto",
                         data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                         files={"photo": ("schedule.png", png, "image/png")})
        return res["message_id"]

    def edit_photo(self, chat_id: str, message_id: int, png: bytes, caption: str) -> None:
        media = {"type": "photo", "media": "attach://photo", "caption": caption, "parse_mode": "HTML"}
        self._call("editMessageMedia",
                   data={"chat_id": chat_id, "message_id": message_id, "media": json.dumps(media, ensure_ascii=False)},
                   files={"photo": ("schedule.png", png, "image/png")})

    def edit_caption(self, chat_id: str, message_id: int, caption: str) -> None:
        self._call("editMessageCaption",
                   data={"chat_id": chat_id, "message_id": message_id,
                         "caption": caption, "parse_mode": "HTML"})

    def delete_message(self, chat_id: str, message_id: int) -> None:
        self._call("deleteMessage", data={"chat_id": chat_id, "message_id": message_id})

    # ── допоміжне ──
    def detect_chat_id(self) -> str | None:
        """Шукає чат, у якому з ботом уже писали (потрібно спершу натиснути /start)."""
        updates = self._call("getUpdates", data={"limit": 20, "timeout": 0})
        for upd in reversed(updates):
            msg = upd.get("message") or upd.get("my_chat_member") or upd.get("edited_message")
            if msg and msg.get("chat", {}).get("id") is not None:
                return str(msg["chat"]["id"])
        return None
