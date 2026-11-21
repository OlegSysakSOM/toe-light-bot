"""Download TOE's public address directory and publish it into Cloudflare D1."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

from cloudflare_d1 import D1Client
from toe_api import HEADERS, load_or_build_address_index, normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("address_sync")


def main() -> None:
    cache_path = Path(os.environ.get("ADDRESS_INDEX_FILE", ".cache/toe_address_index.json"))
    max_pages = int(os.environ.get("MAX_ADDRESS_PAGES", "600"))
    http = requests.Session()
    http.headers.update(HEADERS)
    records = load_or_build_address_index(str(cache_path), session=http, max_pages=max_pages)
    log.info("Підготовлено записів адрес: %d", len(records))

    d1 = D1Client()
    d1.query("DELETE FROM address_index_staging")

    statements: list[dict] = []
    inserted = 0
    for row in records:
        city = row.get("city") or {}
        street = row.get("street") or {}
        source_id = str(row.get("id", "")).strip()
        city_id = str(city.get("id", "")).strip()
        street_id = str(street.get("id", "")).strip()
        if not all((source_id, city_id, street_id)):
            continue
        params = [
            source_id,
            city_id,
            str(city.get("name", "")),
            normalize(str(city.get("name", ""))),
            street_id,
            str(street.get("name", "")),
            normalize(str(street.get("name", ""))),
            str(row.get("buildingNames", "")),
            str(row.get("chergGpv", "")),
        ]
        statements.append({
            "sql": "INSERT OR REPLACE INTO address_index_staging "
                   "(source_id, city_id, city_name, city_norm, street_id, street_name, street_norm, building_names, group_gpv) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            "params": params,
        })
        if len(statements) >= 75:
            d1.batch(statements)
            inserted += len(statements)
            log.info("Завантажено в staging: %d записів", inserted)
            statements = []
    if statements:
        d1.batch(statements)
        inserted += len(statements)

    if inserted == 0:
        raise SystemExit("Довідник не містить жодного коректного запису; чинний індекс не змінено.")

    # Swap only after the complete staging import succeeds, so users keep the
    # previously valid address directory if download/import fails.
    d1.query("DELETE FROM address_index")
    d1.query(
        "INSERT INTO address_index "
        "(source_id, city_id, city_name, city_norm, street_id, street_name, street_norm, building_names, group_gpv) "
        "SELECT source_id, city_id, city_name, city_norm, street_id, street_name, street_norm, building_names, group_gpv "
        "FROM address_index_staging"
    )
    d1.query("DELETE FROM address_index_staging")
    count = d1.query("SELECT COUNT(*) AS total FROM address_index")
    log.info("Синхронізація завершена. У D1 записів: %s", (count[0] or {}).get("total", "?"))


if __name__ == "__main__":
    main()
