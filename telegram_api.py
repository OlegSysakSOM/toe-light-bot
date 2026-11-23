"""Small Telegram Bot API client (long-polling via getUpdates)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

log = logging.getLogger("telegram")


class TelegramError(RuntimeError):
    def __init__(self, code: int | None, description: str):
        super().__init__(f"Telegram {code}: {description}")
        self.code = code
        self.description = description or ""

    @property
    def message_gone(self) -> bool:
        lower = self.description.lower()
        return any(part in lower for part in (
            "message to edit not found", "message_id_invalid", "message can't be edited",
            "message to delete not found", "there is no photo in the request",
        ))

    @property
    def permanent_chat_failure(self) -> bool:
        """True when Telegram says this private destination cannot receive messages anymore."""
        lower = self.description.lower()
        return self.code == 403 and any(part in lower for part in (
            "user is deactivated",
            "bot was blocked by the user",
        ))

    @property
    def not_modified(self) -> bool:
        return "message is not modified" in self.description.lower()


class Telegram:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = requests.Session()

    def _call(self, method: str, *, data: dict[str, Any] | None = None,
              files: dict[str, Any] | None = None, timeout: int = 45,
              retry_429: bool = True) -> Any:
        try:
            response = self.http.post(f"{self.base}/{method}", data=data, files=files, timeout=timeout)
        except requests.RequestException as exc:
            raise TelegramError(None, f"мережева помилка: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramError(response.status_code, response.text[:300]) from exc
        if payload.get("ok"):
            return payload.get("result")
        code = payload.get("error_code")
        description = payload.get("description", "невідома помилка")
        retry_after = (payload.get("parameters") or {}).get("retry_after")
        if code == 429 and retry_after and retry_429:
            wait = min(int(retry_after) + 1, 60)
            log.warning("Telegram rate limit; повтор через %s с", wait)
            time.sleep(wait)
            return self._call(method, data=data, files=files, timeout=timeout, retry_429=False)
        raise TelegramError(code, description)

    def delete_webhook(self) -> None:
        # getUpdates and webhooks are mutually exclusive; keep pending messages.
        self._call("deleteWebhook", data={"drop_pending_updates": "false"})

    def get_updates(self, offset: int, limit: int = 100) -> list[dict[str, Any]]:
        result = self._call("getUpdates", data={
            "offset": offset,
            "limit": max(1, min(100, limit)),
            "timeout": 0,
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }, timeout=30)
        return result or []

    def send_message(self, chat_id: str, text: str,
                     reply_markup: dict[str, Any] | None = None) -> int:
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        result = self._call("sendMessage", data=data)
        return int(result["message_id"])

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        data: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            data["text"] = text[:200]
        self._call("answerCallbackQuery", data=data)

    def send_photo(self, chat_id: str, png: bytes, caption: str) -> int:
        result = self._call("sendPhoto", data={
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
        }, files={"photo": ("schedule.png", png, "image/png")})
        return int(result["message_id"])

    def edit_photo(self, chat_id: str, message_id: int, png: bytes, caption: str) -> None:
        media = {
            "type": "photo",
            "media": "attach://photo",
            "caption": caption,
            "parse_mode": "HTML",
        }
        self._call("editMessageMedia", data={
            "chat_id": chat_id,
            "message_id": message_id,
            "media": json.dumps(media, ensure_ascii=False),
        }, files={"photo": ("schedule.png", png, "image/png")})
