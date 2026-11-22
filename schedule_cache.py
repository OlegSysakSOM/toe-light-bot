"""Upload rendered schedule PNGs to the authenticated Cloudflare Worker cache."""
from __future__ import annotations

import base64
import logging
import os
from datetime import datetime

import requests

log = logging.getLogger("schedule_cache")


class ScheduleCacheUploader:
    def __init__(self, worker_url: str, secret: str) -> None:
        self.url = worker_url.rstrip("/") + "/internal/cache-schedule"
        self.secret = secret
        self.session = requests.Session()

    @classmethod
    def from_env(cls) -> "ScheduleCacheUploader | None":
        worker_url = os.environ.get("WORKER_URL", "").strip()
        secret = os.environ.get("CACHE_UPLOAD_SECRET", "").strip()
        if not worker_url or not secret:
            log.warning(
                "Кеш PNG вимкнено: задайте WORKER_URL та CACHE_UPLOAD_SECRET у GitHub Actions Secrets."
            )
            return None
        return cls(worker_url, secret)

    def put(self, address_key: str, group: str, png: bytes, checked_at: datetime) -> None:
        if not address_key or not group or not png.startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
            raise ValueError("Некоректні дані для кешу графіка.")
        response = self.session.post(
            self.url,
            headers={"Authorization": f"Bearer {self.secret}"},
            json={
                "address_key": address_key,
                "group_name": group,
                "png_base64": base64.b64encode(png).decode("ascii"),
                "cached_at": checked_at.strftime("%d.%m.%Y %H:%M"),
            },
            timeout=(5, 25),
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Worker не підтвердив збереження PNG у R2.")
