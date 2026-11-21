"""Address lookup and outage schedule client for poweron.toe.com.ua."""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("toe")
API_BASE = "https://api-poweron.toe.com.ua/api"
ORIGIN = "https://poweron.toe.com.ua"
ADDRESS_ENDPOINT = f"{API_BASE}/pw_address_queues"
SCHEDULE_ENDPOINT = f"{API_BASE}/a_gpv_g"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": ORIGIN,
    "Referer": f"{ORIGIN}/",
}
RAW_STATUS = {"0": "0", "1": "1", "10": "2"}  # light / outage / possible outage


class ToeAPIError(RuntimeError):
    pass


def normalize(value: str) -> str:
    value = value.lower().replace("ё", "е").replace("’", "'").replace("`", "'")
    value = re.sub(r"[^0-9a-zа-яіїєґ'\-]+", " ", value, flags=re.IGNORECASE)
    tokens = value.split()
    aliases = {
        "вулиця": "вул", "вулиці": "вул", "вул.": "вул",
        "провулок": "пров", "провулка": "пров", "пров.": "пров",
        "проспект": "просп", "просп.": "просп",
        "площа": "пл", "пл.": "пл",
    }
    return " ".join(aliases.get(token, token) for token in tokens)


def _json_members(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], {}
    if isinstance(payload, dict):
        members = payload.get("hydra:member") or payload.get("member") or payload.get("data") or []
        view = payload.get("hydra:view") or payload.get("view") or {}
        if isinstance(members, list):
            return [item for item in members if isinstance(item, dict)], view if isinstance(view, dict) else {}
    raise ToeAPIError("API повернув невідомий формат списку адрес")


def _contains_all(field: str, words: list[str]) -> bool:
    haystack = normalize(field)
    return bool(haystack) and all(word in haystack for word in words)


def _building_matches(building_names: str, requested: str) -> bool:
    wanted = normalize(requested)
    options = [normalize(part) for part in str(building_names or "").split(",")]
    return wanted in options


def load_or_build_address_index(cache_path: str | None = None, *, session: requests.Session | None = None,
                                max_pages: int = 600) -> list[dict[str, Any]]:
    """Load the public TOE address directory from Actions cache or download it once.

    The directory is public data from TOE. It is cached outside the repository so
    a new user's street lookup does not need to scan hundreds of API pages again.
    """
    from pathlib import Path

    path = Path(cache_path) if cache_path else Path(".cache/toe_address_index.json")
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            rows = cached.get("records") if isinstance(cached, dict) else None
            if isinstance(rows, list) and rows:
                log.info("Завантажено адресний довідник з кешу: %d записів", len(rows))
                return [row for row in rows if isinstance(row, dict)]
        except (OSError, ValueError):
            log.warning("Кеш адресного довідника пошкоджено; завантажую його заново")

    http = session or requests.Session()
    http.headers.update(HEADERS)
    all_rows: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    url: str | None = ADDRESS_ENDPOINT
    params: dict[str, Any] | None = {"page": 1, "itemsPerPage": 30}
    page = 0
    while url and page < max_pages:
        page += 1
        last_error: Exception | None = None
        payload: Any = None
        for attempt in range(3):
            try:
                response = http.get(url, params=params, timeout=18)
                response.raise_for_status()
                payload = response.json()
                last_error = None
                break
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.6 * (attempt + 1))
        if last_error is not None:
            raise ToeAPIError(f"Не вдалося завантажити довідник адрес (сторінка {page}): {last_error}")

        members, view = _json_members(payload)
        if not members:
            break
        fingerprint = json.dumps(members, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen_pages:
            raise ToeAPIError(f"API повторив сторінку {page}; повний адресний довідник не завантажено")
        seen_pages.add(fingerprint)
        all_rows.extend(members)

        next_link = view.get("hydra:next") or view.get("next")
        if next_link:
            url = urljoin(ORIGIN, str(next_link))
            params = None
        elif len(members) >= 30:
            url = ADDRESS_ENDPOINT
            params = {"page": page + 1, "itemsPerPage": 30}
        else:
            url, params = None, None
        if page % 25 == 0:
            log.info("Завантаження адресного довідника: %d сторінок, %d записів", page, len(all_rows))

    if page >= max_pages and url:
        raise ToeAPIError(f"Довідник перевищив ліміт {max_pages} сторінок; кеш не збережено")
    if not all_rows:
        raise ToeAPIError("Сайт повернув порожній адресний довідник")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"records": all_rows}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    log.info("Адресний довідник завантажено: %d сторінок, %d записів", page, len(all_rows))
    return all_rows


def find_street_records(city: str, street: str, *, session: requests.Session | None = None,
                        max_pages: int = 600, cached_records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return API address-queue rows filtered by city and street."""
    city_words = normalize(city).split()
    street_words = normalize(street).split()
    if not city_words or not street_words:
        raise ValueError("Вкажіть і населений пункт, і вулицю")

    if cached_records is not None:
        source_records = cached_records
    else:
        http = session or requests.Session()
        http.headers.update(HEADERS)
        source_records = []
        seen_pages: set[str] = set()
        url: str | None = ADDRESS_ENDPOINT
        params: dict[str, Any] | None = {"page": 1, "itemsPerPage": 30}
        page = 0
        while url and page < max_pages:
            page += 1
            last_error: Exception | None = None
            response_payload: Any = None
            for attempt in range(3):
                try:
                    response = http.get(url, params=params, timeout=18)
                    response.raise_for_status()
                    response_payload = response.json()
                    last_error = None
                    break
                except (requests.RequestException, ValueError) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(0.6 * (attempt + 1))
            if last_error is not None:
                raise ToeAPIError(f"Не вдалося прочитати довідник адрес (сторінка {page}): {last_error}")

            members, view = _json_members(response_payload)
            if not members:
                break
            fingerprint = json.dumps(members, ensure_ascii=False, sort_keys=True)
            if fingerprint in seen_pages:
                log.warning("API повторив сторінку %s; зупиняю пагінацію", page)
                break
            seen_pages.add(fingerprint)
            source_records.extend(members)

            next_link = view.get("hydra:next") or view.get("next")
            if next_link:
                url = urljoin(ORIGIN, str(next_link))
                params = None
            elif isinstance(response_payload, list) and len(members) >= 30:
                url = ADDRESS_ENDPOINT
                params = {"page": page + 1, "itemsPerPage": 30}
            elif isinstance(response_payload, dict) and len(members) >= 30:
                url = ADDRESS_ENDPOINT
                params = {"page": page + 1, "itemsPerPage": 30}
            else:
                url, params = None, None
            if page % 25 == 0:
                log.info("Пошук адрес: сторінка %d, отримано записів %d", page, len(source_records))

        if page >= max_pages and url:
            raise ToeAPIError(
                f"Пошук зупинено після {max_pages} сторінок. Збільште MAX_ADDRESS_PAGES або повторіть пізніше."
            )

    matches: list[dict[str, Any]] = []
    for row in source_records:
        city_name = str((row.get("city") or {}).get("name", ""))
        street_name = str((row.get("street") or {}).get("name", ""))
        if _contains_all(city_name, city_words) and _contains_all(street_name, street_words):
            matches.append(row)
    return matches


def find_address_matches(city: str, street: str, building: str, *, session: requests.Session | None = None,
                         max_pages: int = 600, cached_records: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    records = find_street_records(city, street, session=session, max_pages=max_pages, cached_records=cached_records)
    matches: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in records:
        city_obj = row.get("city") or {}
        street_obj = row.get("street") or {}
        city_id = str(city_obj.get("id", "")).strip()
        street_id = str(street_obj.get("id", "")).strip()
        if not city_id or not street_id or not _building_matches(str(row.get("buildingNames", "")), building):
            continue
        group = str(row.get("chergGpv", "")).strip()
        label = f"{city_obj.get('name', city)} · {street_obj.get('name', street)} · {building}"
        identity = (city_id, street_id, group)
        if identity in seen:
            continue
        seen.add(identity)
        matches.append({
            "address_key": f"{city_id}/{street_id}/{building.strip()}",
            "city": str(city_obj.get("name", city)),
            "street": str(street_obj.get("name", street)),
            "building": building.strip(),
            "group": group,
            "label": label,
        })
    return matches


def fetch_schedule(address_key: str, group: str, tz: ZoneInfo,
                   session: requests.Session | None = None, timeout: int = 25) -> dict[str, dict[str, str]]:
    """Return {YYYY-MM-DD: {slots: 48-char string, created: ...}} for today/tomorrow."""
    http = session or requests.Session()
    now = datetime.now(tz)
    today = now.date()
    after = (now - timedelta(days=1)).strftime("%Y-%m-%dT12:00:00+00:00")
    before = (now + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59+00:00")
    headers = dict(HEADERS)
    headers["X-debug-key"] = base64.b64encode(address_key.encode("utf-8")).decode("ascii")
    params = {
        "before": before,
        "after": after,
        "group[]": group,
        "time": address_key.replace("/", ""),
    }
    try:
        response = http.get(SCHEDULE_ENDPOINT, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ToeAPIError(f"Не вдалося отримати графік: {exc}") from exc

    if isinstance(payload, list):
        members = payload
    elif isinstance(payload, dict):
        members = payload.get("hydra:member") or payload.get("member") or []
    else:
        raise ToeAPIError("API графіків повернув невідомий формат")

    found: dict[str, dict[str, str]] = {}
    for member in members:
        data = member.get("dataJson") if isinstance(member, dict) else None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                continue
        if not isinstance(data, dict):
            continue
        group_data = data.get(group)
        if not isinstance(group_data, dict):
            group_data = next((v for k, v in data.items() if str(k).strip() == group.strip()), None)
        if not isinstance(group_data, dict):
            continue
        day_raw = str(member.get("dateGraph", ""))[:10]
        try:
            day = date.fromisoformat(day_raw)
        except ValueError:
            continue
        if not (today <= day <= today + timedelta(days=1)):
            continue
        times = group_data.get("times") or {}
        slots: list[str] = []
        for hour in range(24):
            for minute in (0, 30):
                raw = str(times.get(f"{hour:02d}:{minute:02d}", "0")).strip()
                slots.append(RAW_STATUS.get(raw, "3"))
        created = str(member.get("dateCreate", ""))
        key = day.isoformat()
        if key not in found or created >= found[key]["created"]:
            found[key] = {"slots": "".join(slots), "created": created}
    return found
