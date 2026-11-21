"""Minimal Cloudflare D1 REST API client used by GitHub scheduled jobs."""
from __future__ import annotations

import os
from typing import Any

import requests


class D1Error(RuntimeError):
    pass


class D1Client:
    def __init__(self) -> None:
        self.token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        self.account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        self.database_id = os.environ.get("CLOUDFLARE_D1_DATABASE_ID", "").strip()
        if not all((self.token, self.account_id, self.database_id)):
            raise D1Error("Потрібні CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID та CLOUDFLARE_D1_DATABASE_ID.")
        self.url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/d1/database/{self.database_id}/query"
        )
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    @staticmethod
    def _check(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not payload.get("success"):
            errors = payload.get("errors") or []
            raise D1Error("Cloudflare D1: " + "; ".join(str(e.get("message", e)) for e in errors))
        result = payload.get("result") or []
        return result if isinstance(result, list) else [result]

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"sql": sql}
        if params is not None:
            body["params"] = params
        response = self.session.post(self.url, json=body, timeout=60)
        response.raise_for_status()
        result = self._check(response.json())
        if not result:
            return []
        first = result[0] or {}
        return first.get("results") or []

    def batch(self, statements: list[dict[str, Any]]) -> None:
        if not statements:
            return
        response = self.session.post(self.url, json={"batch": statements}, timeout=120)
        response.raise_for_status()
        self._check(response.json())
