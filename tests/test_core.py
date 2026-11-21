from datetime import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json

from cryptography.fernet import Fernet
from zoneinfo import ZoneInfo

from render import render_schedule_png
from state_store import StateStore, empty_state
from toe_api import find_address_matches, normalize


def test_address_lookup_uses_real_toe_row_shape():
    rows = [{
        "id": 8950,
        "city": {"id": 22016, "name": "Велика Лука"},
        "street": {"id": 40637, "name": "вул. Івана Франка"},
        "buildingNames": "1, 10, 11, 12, 24, 25",
        "chergGpv": "5.1",
    }, {
        "id": 9,
        "city": {"id": 22017, "name": "Інше село"},
        "street": {"id": 9, "name": "вул. Франка"},
        "buildingNames": "24",
        "chergGpv": "1.1",
    }]
    found = find_address_matches("Велика Лука", "вулиця Франка", "24", cached_records=rows)
    assert len(found) == 1
    assert found[0]["address_key"] == "22016/40637/24"
    assert found[0]["group"] == "5.1"


def test_normalize_street_abbreviation():
    assert normalize("вулиця Івана Франка") == normalize("вул. Івана Франка")


def test_render_poster_is_png():
    slots = "0" * 12 + "1" * 8 + "0" * 20 + "2" * 8
    png = render_schedule_png(
        {"2026-11-21": {"slots": slots, "created": "test"}},
        "Велика Лука · вул. Івана Франка · 24",
        "5.1",
        datetime.now(ZoneInfo("Europe/Kyiv")),
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 10_000


def test_encrypted_state_roundtrip_and_old_state_migration(tmp_path: Path):
    key = Fernet.generate_key().decode()
    store = StateStore(tmp_path / "state.enc", key)
    state = empty_state()
    state["users"]["123"] = {"active": True, "group": "5.1"}
    state["update_offset"] = 77
    store.save(state)
    loaded = store.load()
    assert loaded["users"]["123"]["group"] == "5.1"
    assert loaded["update_offset"] == 77

    legacy_path = tmp_path / "legacy.enc"
    legacy = {"chat_id": "719882819", "message_id": 1, "snapshot": {}}
    legacy_path.write_bytes(Fernet(key.encode()).encrypt(json.dumps(legacy).encode()))
    migrated = StateStore(legacy_path, key).load()
    assert migrated["schema_version"] == 2
    assert migrated["users"] == {}
    assert migrated["bootstrap_complete"] is False


def test_address_index_download_pagination_and_cache(tmp_path: Path):
    from toe_api import load_or_build_address_index, ADDRESS_ENDPOINT

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self.payload

    class FakeSession:
        def __init__(self):
            self.calls = []
            self.headers = {}
        def get(self, url, params=None, timeout=18):
            self.calls.append((url, params))
            page = (params or {}).get("page", 1)
            if page == 1:
                rows = [{"id": i} for i in range(30)]
            else:
                rows = [{"id": 31}, {"id": 32}]
            return FakeResponse(rows)

    path = tmp_path / "index.json"
    fake = FakeSession()
    rows = load_or_build_address_index(str(path), session=fake, max_pages=10)
    assert len(rows) == 32
    assert len(fake.calls) == 2

    # Second call uses the saved local cache and makes no HTTP requests.
    fake2 = FakeSession()
    again = load_or_build_address_index(str(path), session=fake2, max_pages=10)
    assert again == rows
    assert fake2.calls == []


def test_fetch_schedule_parses_toe_payload():
    from datetime import timedelta
    from toe_api import fetch_schedule

    tz = ZoneInfo("Europe/Kyiv")
    today = datetime.now(tz).date().isoformat()
    slots = {f"{hour:02d}:{minute:02d}": "0" for hour in range(24) for minute in (0, 30)}
    slots["07:00"] = "1"
    slots["07:30"] = "10"
    payload = [{
        "dateGraph": today,
        "dateCreate": "2026-11-21T10:00:00",
        "dataJson": json.dumps({"5.1": {"times": slots}}),
    }]

    class FakeResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return payload

    class FakeSession:
        def get(self, url, params=None, headers=None, timeout=25):
            assert params["time"] == "220164063724"
            assert params["group[]"] == "5.1"
            return FakeResponse()

    schedules = fetch_schedule("22016/40637/24", "5.1", tz, session=FakeSession())
    assert schedules[today]["slots"][14] == "1"
    assert schedules[today]["slots"][15] == "2"


def test_private_users_get_independent_subscriptions(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import bot as bot_module
    from state_store import empty_state

    address_rows = [{
        "id": 8950,
        "city": {"id": 22016, "name": "Велика Лука"},
        "street": {"id": 40637, "name": "вул. Івана Франка"},
        "buildingNames": "1, 10, 11, 12, 24, 25",
        "chergGpv": "5.1",
    }]
    monkeypatch.setattr(bot_module, "load_or_build_address_index", lambda *a, **k: address_rows)

    class FakeTelegram:
        def __init__(self):
            self.messages = []
        def send_message(self, chat_id, text):
            self.messages.append((chat_id, text))
            return len(self.messages)

    tg = FakeTelegram()
    cfg = SimpleNamespace(address_index_file=tmp_path / "address.json", max_address_pages=600)
    state = empty_state()
    http = object()
    for chat, inputs in {
        "10001": ["/start", "Велика Лука", "Івана Франка", "24"],
        "10002": ["/start", "Велика Лука", "Івана Франка", "24"],
    }.items():
        for text in inputs:
            bot_module._handle_text(chat, text, state, tg, cfg, http)

    assert state["users"]["10001"]["active"] is True
    assert state["users"]["10002"]["active"] is True
    assert state["users"]["10001"]["address_key"] == "22016/40637/24"
    assert state["users"]["10002"]["group"] == "5.1"
    assert state["users"]["10001"] is not state["users"]["10002"]
