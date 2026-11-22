"""Upload rendered schedule PNGs to the authenticated Cloudflare D1 BLOB cache."""
from __future__ import annotations

import base64
import logging
import os
from datetime import datetime
from typing import Any

import requests

log = logging.getLogger("schedule_cache")


class ScheduleCacheUploader:
    def __init__(self, worker_url: str, secret: str) -> None:
        self.url = worker_url.rstrip("/") + "/internal/cache-schedule"
        self.secret = secret
        self.session = requests.Session()

    @classmethod
    def from_env(cls) -> "ScheduleCacheUploader":
        worker_url = os.environ.get("WORKER_URL", "").strip()
        secret = os.environ.get("CACHE_UPLOAD_SECRET", "").strip()
        if not worker_url or not secret:
            raise RuntimeError(
                "Кеш PNG не налаштований: додайте WORKER_URL та CACHE_UPLOAD_SECRET "
                "у GitHub Actions Secrets."
            )
        return cls(worker_url, secret)

    def put(
        self,
        address_key: str,
        group: str,
        png: bytes,
        checked_at: datetime,
        address_label: str,
        snapshot: dict[str, str],
    ) -> None:
        if not address_key or not group or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Некоректні дані для кешу графіка.")
        if len(png) > 1_950_000:
            raise ValueError("PNG перевищує безпечний ліміт D1 BLOB 1.95 MB.")
        payload: dict[str, Any] = {
            "address_key": address_key,
            "group_name": group,
            "address_label": address_label,
            "png_base64": base64.b64encode(png).decode("ascii"),
            "cached_at": checked_at.strftime("%d.%m.%Y %H:%M"),
            "cached_at_ms": int(checked_at.timestamp() * 1000),
            "snapshot": snapshot,
        }
        response = self.session.post(
            self.url,
            headers={"Authorization": f"Bearer {self.secret}"},
            json=payload,
            timeout=(5, 25),
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError("Worker не підтвердив збереження PNG у D1.")
