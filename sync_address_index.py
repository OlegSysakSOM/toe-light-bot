"""Download TOE's address directory and update only changed D1 rows.

The directory is ~12k rows. A previous full replacement wrote every address
into staging, deleted the live table, copied all rows back, then deleted
staging. Because D1 counts secondary-index writes too, this could consume most
of the Free daily write quota even when the address directory had not changed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import requests

from cloudflare_d1 import D1Client
from toe_api import HEADERS, load_or_build_address_index, normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("address_sync")

COLUMNS = (
    "source_id", "city_id", "city_name", "city_norm", "street_id",
    "street_name", "street_norm", "building_names", "group_gpv",
)


def prepare_records(records: list[dict[str, Any]]) -> tuple[list[tuple[str, ...]], str]:
    """Normalize records, sort deterministically, and return a content digest."""
    by_id: dict[str, tuple[str, ...]] = {}
    for row in records:
        city = row.get("city") or {}
        street = row.get("street") or {}
        source_id = str(row.get("id", "")).strip()
        city_id = str(city.get("id", "")).strip()
        street_id = str(street.get("id", "")).strip()
        if not all((source_id, city_id, street_id)):
            continue
        by_id[source_id] = (
            source_id,
            city_id,
            str(city.get("name", "")),
            normalize(str(city.get("name", ""))),
            street_id,
            str(street.get("name", "")),
            normalize(str(street.get("name", ""))),
            str(row.get("buildingNames", "")),
            str(row.get("chergGpv", "")),
        )

    prepared = [by_id[key] for key in sorted(by_id)]
    if not prepared:
        raise ValueError("Довідник не містить жодного коректного запису.")

    canonical = json.dumps(prepared, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return prepared, hashlib.sha256(canonical).hexdigest()


def _chunks(items: list[tuple[str, ...]], size: int = 75):
    for offset in range(0, len(items), size):
        yield items[offset:offset + size]


def diff_records(
    prepared: list[tuple[str, ...]],
    existing_rows: list[dict[str, Any]],
) -> tuple[list[tuple[str, ...]], list[str], int]:
    """Return changed/new rows, stale source IDs, and unchanged count."""
    existing = {
        str(row["source_id"]): tuple(str(row.get(column) or "") for column in COLUMNS)
        for row in existing_rows
    }
    incoming = {row[0]: row for row in prepared}
    unchanged = sum(1 for source_id, row in incoming.items() if existing.get(source_id) == row)
    changed = [row for source_id, row in incoming.items() if existing.get(source_id) != row]
    removed = sorted(set(existing) - set(incoming))
    return changed, removed, unchanged


def main() -> None:
    cache_path = Path(os.environ.get("ADDRESS_INDEX_FILE", ".cache/toe_address_index.json"))
    max_pages = int(os.environ.get("MAX_ADDRESS_PAGES", "600"))
    http = requests.Session()
    http.headers.update(HEADERS)
    records = load_or_build_address_index(str(cache_path), session=http, max_pages=max_pages)
    prepared, digest = prepare_records(records)
    log.info("Підготовлено адрес у довіднику TOE: %d; sha256=%s", len(prepared), digest[:12])

    d1 = D1Client()
    existing_rows = d1.query(
        "SELECT source_id, city_id, city_name, city_norm, street_id, street_name, "
        "street_norm, building_names, group_gpv FROM address_index"
    )
    changed, removed, unchanged = diff_records(prepared, existing_rows)
    existing_ids = {str(row["source_id"]) for row in existing_rows}

    if not changed and not removed:
        log.info(
            "Довідник не змінився: %d адрес. D1 writes пропущено; rows_read=%d rows_written=%d.",
            len(prepared), d1.rows_read_total, d1.rows_written_total,
        )
        return

    placeholders = ", ".join("?" for _ in COLUMNS)
    update_columns = COLUMNS[1:]
    set_clause = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
    difference_clause = " OR ".join(
        f"address_index.{column} IS NOT excluded.{column}" for column in update_columns
    )
    upsert_sql = (
        f"INSERT INTO address_index ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(source_id) DO UPDATE SET {set_clause} WHERE {difference_clause}"
    )

    inserted = sum(1 for row in changed if row[0] not in existing_ids)
    updated = len(changed) - inserted
    statements: list[dict[str, Any]] = []
    for row in changed:
        statements.append({"sql": upsert_sql, "params": list(row)})
        if len(statements) >= 75:
            d1.batch(statements)
            statements = []
    if statements:
        d1.batch(statements)

    deleted = 0
    for offset in range(0, len(removed), 75):
        batch_ids = removed[offset:offset + 75]
        marks = ", ".join("?" for _ in batch_ids)
        d1.query(f"DELETE FROM address_index WHERE source_id IN ({marks})", batch_ids)
        deleted += len(batch_ids)

    d1.query(
        "INSERT INTO bot_meta(key, value) VALUES ('address_index_sha256', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE bot_meta.value IS NOT excluded.value",
        [digest],
    )

    count = d1.query("SELECT COUNT(*) AS total FROM address_index")
    total = int((count[0] or {}).get("total", 0))
    if total != len(prepared):
        raise RuntimeError(
            f"Неповна синхронізація адрес: у D1 {total}, у джерелі {len(prepared)}."
        )

    log.info(
        "Синхронізація завершена: всього=%d, без змін=%d, нових=%d, оновлених=%d, "
        "видалених=%d; D1 API queries=%d rows_read=%d rows_written=%d.",
        total, unchanged, inserted, updated, deleted,
        d1.queries_total, d1.rows_read_total, d1.rows_written_total,
    )


if __name__ == "__main__":
    main()
