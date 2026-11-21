"""Допомагає дізнатися TOE_ADDRESS (cityId/streetId/будинок) для вашої адреси.

    python find_address.py "назва населеного пункту" "назва вулиці" --building "номер"

Скрипт гортає відкритий список адрес сайту (pw_address_queues) і показує записи, де є всі слова.
Якщо ID не вдалося визначити автоматично — див. у README «Запасний спосіб через DevTools».
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.parse import urljoin

import requests

from toe_api import API_BASE, HEADERS

ORIGIN = "https://api-poweron.toe.com.ua"


def _walk(obj, path=""):
    """Плоский перелік (шлях, значення) для вкладеного JSON."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def _guess_ids(record: dict) -> tuple[str | None, str | None]:
    city = street = None
    for path, value in _walk(record):
        p = path.lower()
        if p.endswith("id") and value is not None and isinstance(value, (int, str)):
            if "city" in p or "settlement" in p:
                city = city or str(value)
            elif "street" in p or "streer" in p:
                street = street or str(value)
    return city, street


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("words", nargs="+", help="слова для пошуку в адресі")
    ap.add_argument("--building", required=True, help="номер будинку")
    ap.add_argument("--max-pages", type=int, default=1000)
    args = ap.parse_args()
    # Розбиваємо фрази на окремі слова: "Велика Лука" -> "велика", "лука".
    words = [part.lower() for item in args.words for part in item.split() if part.strip()]

    http = requests.Session()
    http.headers.update(HEADERS)
    url, params = f"{API_BASE}/pw_address_queues", {"page": 1, "itemsPerPage": 1000}
    matches, page, scanned = [], 0, 0
    seen_pages: set[str] = set()

    print(f"Шукаю записи, де є всі слова: {args.words} …")
    try:
        while url and page < args.max_pages:
            page += 1
            # Деякі версії API повертають звичайний масив по 30 записів
            # без hydra:view. У такому разі перегортаємо сторінки параметром page.
            resp = http.get(url, params=params or {"page": page + 1, "itemsPerPage": 1000}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            # API може повертати як звичайний JSON-масив, так і Hydra-об'єкт.
            if isinstance(data, list):
                members = data
                view = {}
                plain_list = True
            elif isinstance(data, dict):
                members = data.get("hydra:member") or data.get("member") or []
                view = data.get("hydra:view") or data.get("view") or {}
                plain_list = False
            else:
                raise ValueError(f"Неочікуваний формат відповіді API: {type(data).__name__}")

            if not isinstance(members, list):
                raise ValueError("Поле зі списком адрес у відповіді API має неочікуваний формат")
            # Захист від API, який ігнорує page і щоразу повертає ту саму сторінку.
            fingerprint = json.dumps(members, ensure_ascii=False, sort_keys=True)
            if fingerprint in seen_pages:
                print("\nAPI повторно повернув ту саму сторінку; зупиняю перегортання.")
                break
            seen_pages.add(fingerprint)
            scanned += len(members)
            for rec in members:
                if not isinstance(rec, (dict, list)):
                    continue
                blob = json.dumps(rec, ensure_ascii=False).lower()
                if all(w in blob for w in words):
                    matches.append(rec)
            nxt = view.get("hydra:next") or view.get("next") if isinstance(view, dict) else None
            if nxt:
                url, params = urljoin(ORIGIN, nxt), None
            elif plain_list and len(members) >= 30:
                url, params = f"{API_BASE}/pw_address_queues", {"page": page + 1, "itemsPerPage": 1000}
            else:
                url, params = None, None
            print(f"\r  сторінка {page}, переглянуто записів: {scanned}, збігів: {len(matches)}", end="", flush=True)
            time.sleep(0.15)
    except KeyboardInterrupt:
        print("\n(перервано)")
    except requests.RequestException as exc:
        print(f"\nПомилка запиту: {exc}")
        sys.exit(1)
    except (ValueError, TypeError) as exc:
        print(f"\nПомилка формату відповіді API: {exc}")
        sys.exit(1)
    print()

    if not matches:
        print("Нічого не знайдено. Спробуйте інші слова (наприклад, лише назву вулиці) "
              "або скористайтеся запасним способом через DevTools (README).")
        return

    print(f"\nЗнайдено записів: {len(matches)}. Перший запис повністю:\n")
    print(json.dumps(matches[0], ensure_ascii=False, indent=2)[:3000])

    print("\nМожливі значення для .env:")
    seen = set()
    for rec in matches:
        city, street = _guess_ids(rec)
        if city and street and (city, street) not in seen:
            seen.add((city, street))
            print(f"  TOE_ADDRESS={city}/{street}/{args.building}")
    if not seen:
        print("  (ID автоматично не визначились — вони є у JSON вище: шукайте поля з id для міста й вулиці)")
    print("\nПеревірте результат: python bot.py --preview — там має з’явитися ваш графік.")


if __name__ == "__main__":
    main()
