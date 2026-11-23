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
        def send_message(self, chat_id, text, reply_markup=None):
            self.messages.append((chat_id, text, reply_markup))
            return len(self.messages)
        def answer_callback_query(self, callback_query_id, text=""):
            self.callbacks = getattr(self, "callbacks", [])
            self.callbacks.append((callback_query_id, text))

    tg = FakeTelegram()
    cfg = SimpleNamespace(address_index_file=tmp_path / "address.json", max_address_pages=600)
    state = empty_state()
    http = object()
    for chat, inputs in {
        "10001": ["/start", bot_module.BTN_ADD, "Велика Лука", "Івана Франка", "24"],
        "10002": ["/start", bot_module.BTN_ADD, "Велика Лука", "Івана Франка", "24"],
    }.items():
        for text in inputs:
            bot_module._handle_text(chat, text, state, tg, cfg, http)

    assert state["users"]["10001"]["active"] is True
    assert state["users"]["10002"]["active"] is True
    assert state["users"]["10001"]["address_key"] == "22016/40637/24"
    assert state["users"]["10002"]["group"] == "5.1"
    assert state["users"]["10001"] is not state["users"]["10002"]


def test_menu_buttons_start_and_change_address_without_slash_commands(monkeypatch, tmp_path: Path):
    from types import SimpleNamespace
    import bot as bot_module
    from state_store import empty_state

    address_rows = [{
        "id": 8950,
        "city": {"id": 22016, "name": "Велика Лука"},
        "street": {"id": 40637, "name": "вул. Івана Франка"},
        "buildingNames": "24",
        "chergGpv": "5.1",
    }]
    monkeypatch.setattr(bot_module, "load_or_build_address_index", lambda *a, **k: address_rows)

    class FakeTelegram:
        def __init__(self):
            self.messages = []
        def send_message(self, chat_id, text, reply_markup=None):
            self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
            return len(self.messages)

    tg = FakeTelegram()
    cfg = SimpleNamespace(address_index_file=tmp_path / "address.json", max_address_pages=600)
    state = empty_state()
    http = object()
    chat = "50001"

    # Initial subscription starts by pressing the menu button, not /start.
    bot_module._handle_text(chat, bot_module.BTN_ADD, state, tg, cfg, http)
    user = state["users"][chat]
    assert user["flow"] == "city"
    opening_buttons = [button["text"] for row in tg.messages[-1]["reply_markup"]["keyboard"] for button in row]
    assert bot_module.BTN_CANCEL in opening_buttons
    assert bot_module.BTN_ADD in opening_buttons
    assert bot_module.BTN_CHANGE in opening_buttons

    for value in ("Велика Лука", "Івана Франка", "24"):
        bot_module._handle_text(chat, value, state, tg, cfg, http)
    assert user["active"] is True
    assert user["group"] == "5.1"
    assert state["force_schedule_check"] is True
    assert bot_module.BTN_CHANGE in [
        button["text"]
        for row in tg.messages[-1]["reply_markup"]["keyboard"]
        for button in row
    ]

    # Change-address button starts a new flow but keeps the old subscription
    # active until the new address is confirmed.
    bot_module._handle_text(chat, bot_module.BTN_CHANGE, state, tg, cfg, http)
    assert user["flow"] == "city"
    assert user["active"] is True
    assert user["address_key"] == "22016/40637/24"


def test_inline_address_choice_callback_activates_subscription():
    import bot as bot_module
    from state_store import empty_state

    class FakeTelegram:
        def __init__(self):
            self.answered = []
            self.messages = []
        def answer_callback_query(self, callback_query_id, text=""):
            self.answered.append((callback_query_id, text))
        def send_message(self, chat_id, text, reply_markup=None):
            self.messages.append((chat_id, text, reply_markup))
            return len(self.messages)

    state = empty_state()
    state["users"]["70001"] = {
        "active": False, "flow": "choose", "pending_matches": [
            {"address_key": "22016/40637/24", "label": "Велика Лука · вул. Івана Франка · 24", "group": "5.1"}
        ],
        "snapshot": {}, "message_id": None,
    }
    tg = FakeTelegram()
    callback = {
        "id": "cb-1",
        "data": "choose_address:0",
        "message": {"chat": {"id": 70001, "type": "private"}},
    }
    bot_module._handle_callback(callback, state, tg)
    assert state["users"]["70001"]["active"] is True
    assert state["users"]["70001"]["group"] == "5.1"
    assert state["force_schedule_check"] is True
    assert tg.answered


def test_schedule_refresh_is_throttled_separately_from_inbox_polling():
    import bot as bot_module
    state = {"last_schedule_check_at": 1_000, "force_schedule_check": False}
    assert bot_module.schedule_check_due(state, 1_600, 900) is False
    assert bot_module.schedule_check_due(state, 1_900, 900) is True
    state["force_schedule_check"] = True
    assert bot_module.schedule_check_due(state, 1_100, 900) is True


def test_state_store_does_not_reencrypt_unchanged_data(tmp_path: Path):
    from cryptography.fernet import Fernet
    from state_store import StateStore, empty_state

    key = Fernet.generate_key().decode()
    path = tmp_path / "state.enc"
    store = StateStore(path, key)
    state = empty_state()
    store.save(state)
    first_payload = path.read_bytes()
    store.save(state)
    second_payload = path.read_bytes()
    assert first_payload == second_payload


def test_button_keyboard_always_shows_full_menu_even_during_address_entry():
    import bot as bot_module

    keyboard_flow = bot_module._reply_keyboard({"flow": "street", "address_key": ""})
    flow_texts = [button["text"] for row in keyboard_flow["keyboard"] for button in row]
    assert bot_module.BTN_CANCEL in flow_texts
    assert bot_module.BTN_HELP in flow_texts
    assert bot_module.BTN_ADD in flow_texts
    assert bot_module.BTN_CHANGE in flow_texts
    assert bot_module.BTN_STATUS in flow_texts

    keyboard_active = bot_module._reply_keyboard({"flow": None, "address_key": "22016/40637/24", "active": True})
    active_texts = [button["text"] for row in keyboard_active["keyboard"] for button in row]
    assert bot_module.BTN_CHANGE in active_texts
    assert bot_module.BTN_STATUS in active_texts
    assert bot_module.BTN_STOP in active_texts


def test_process_updates_handles_start_without_clearing_pending_updates(tmp_path: Path):
    from types import SimpleNamespace
    import bot as bot_module
    from state_store import empty_state

    class FakeTelegram:
        def __init__(self):
            self.batches = [[{
                "update_id": 41,
                "message": {
                    "chat": {"id": 888, "type": "private"},
                    "text": "/start",
                },
            }], []]
            self.messages = []
        def get_updates(self, offset):
            assert offset in (0, 42)
            return self.batches.pop(0)
        def send_message(self, chat_id, text, reply_markup=None):
            self.messages.append((str(chat_id), text, reply_markup))
            return len(self.messages)

    class FakeStore:
        def __init__(self):
            self.saved = []
        def save(self, state):
            self.saved.append(json.loads(json.dumps(state)))

    state = empty_state()
    state["bootstrap_complete"] = False
    tg = FakeTelegram()
    store = FakeStore()
    cfg = SimpleNamespace(address_index_file=tmp_path / "unused.json", max_address_pages=600)
    bot_module.process_updates(tg, state, cfg, store, object())

    assert state["update_offset"] == 42
    assert state["users"]["888"]["flow"] is None
    assert any("Вітаю" in text for _, text, _ in tg.messages)
    assert any(
        button["text"] == bot_module.BTN_ADD
        for _, _, markup in tg.messages
        for row in markup["keyboard"]
        for button in row
    )
    assert store.saved


def test_telegram_client_sends_reply_keyboard_and_requests_callbacks(monkeypatch):
    from telegram_api import Telegram
    import json

    tg = Telegram("123:test-token")
    calls = []

    def fake_call(method, *, data=None, files=None, timeout=45, retry_429=True):
        calls.append((method, data, files))
        if method == "sendMessage":
            return {"message_id": 77}
        if method == "getUpdates":
            return []
        return True

    monkeypatch.setattr(tg, "_call", fake_call)
    markup = {"keyboard": [[{"text": "➕ Додати адресу"}]], "resize_keyboard": True}
    assert tg.send_message("555", "Привіт", reply_markup=markup) == 77
    assert json.loads(calls[0][1]["reply_markup"]) == markup

    tg.get_updates(5)
    allowed = json.loads(calls[1][1]["allowed_updates"])
    assert "message" in allowed
    assert "callback_query" in allowed


def test_state_store_reload_does_not_create_artificial_commit(tmp_path: Path):
    from cryptography.fernet import Fernet
    from state_store import StateStore, empty_state

    key = Fernet.generate_key().decode()
    path = tmp_path / "state.enc"
    first = StateStore(path, key)
    first.save(empty_state())
    original_bytes = path.read_bytes()

    second = StateStore(path, key)
    reloaded = second.load()
    second.save(reloaded)
    assert path.read_bytes() == original_bytes


def test_background_refresh_sends_only_on_real_schedule_change(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    import bot as bot_module

    class FakeTelegram:
        def __init__(self):
            self.photos = []
            self.edits = []
            self.messages = []
        def send_photo(self, chat_id, png, caption):
            self.photos.append((chat_id, png, caption))
            return 99
        def edit_photo(self, *args):
            self.edits.append(args)
            raise AssertionError("Automatic refresh must never edit old messages")
        def send_message(self, *args, **kwargs):
            self.messages.append(args)

    class FakeStore:
        def save(self, state):
            pass

    tz = ZoneInfo("Europe/Kyiv")
    day = datetime.now(tz).date().isoformat()
    same = {day: {"slots": "0" * 48, "created": "same"}}
    changed = {day: {"slots": "1" + "0" * 47, "created": "different"}}
    monkeypatch.setattr(bot_module, "render_schedule_png", lambda *a, **k: b"png")
    state = {"users": {"555": {
        "active": True, "address_key": "22016/40637/24", "group": "5.1",
        "address_label": "Велика Лука · Франка · 24",
        "snapshot": {day: "0" * 48}, "message_id": 77,
        "fail_count": 0, "error_notified": False,
    }}}
    tg = FakeTelegram()

    monkeypatch.setattr(bot_module, "fetch_schedule", lambda *a, **k: same)
    stats = bot_module.process_subscriptions(tg, state, SimpleNamespace(), tz, object(), FakeStore())
    assert stats["unchanged"] == 1
    assert tg.photos == []
    assert tg.edits == []
    assert tg.messages == []

    monkeypatch.setattr(bot_module, "fetch_schedule", lambda *a, **k: changed)
    stats = bot_module.process_subscriptions(tg, state, SimpleNamespace(), tz, object(), FakeStore())
    assert stats["sent"] == 1
    assert len(tg.photos) == 1
    assert tg.edits == []
    assert state["users"]["555"]["message_id"] == 99
    assert state["users"]["555"]["snapshot"][day] == changed[day]["slots"]


def test_background_refresh_establishes_first_snapshot_silently(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    import bot as bot_module

    class FakeTelegram:
        def send_photo(self, *args):
            raise AssertionError("Initial baseline must not send a notification")
        def edit_photo(self, *args):
            raise AssertionError("Initial baseline must not edit a message")
        def send_message(self, *args, **kwargs):
            raise AssertionError("Initial baseline must be silent")

    class FakeStore:
        def save(self, state):
            pass

    tz = ZoneInfo("Europe/Kyiv")
    day = datetime.now(tz).date().isoformat()
    schedule = {day: {"slots": "0" * 48, "created": "now"}}
    monkeypatch.setattr(bot_module, "fetch_schedule", lambda *a, **k: schedule)
    state = {"users": {"555": {
        "active": True, "address_key": "22016/40637/24", "group": "5.1",
        "address_label": "Велика Лука · Франка · 24", "snapshot": {},
    }}}
    stats = bot_module.process_subscriptions(FakeTelegram(), state, SimpleNamespace(), tz, object(), FakeStore())
    assert stats["baselined"] == 1
    assert state["users"]["555"]["snapshot"][day] == "0" * 48


def test_reference_png_is_600px_wide():
    from datetime import datetime
    from io import BytesIO
    from PIL import Image
    from render import render_schedule_png

    tz = ZoneInfo("Europe/Kyiv")
    now = datetime.now(tz)
    day = now.date().isoformat()
    slots = "0" * 16 + "1" * 8 + "2" * 4 + "0" * 20
    png = render_schedule_png(
        {day: {"slots": slots, "created": "test"}},
        "Велика Лука · вул. Івана Франка, 24", "5.1", now,
    )
    with Image.open(BytesIO(png)) as image:
        assert image.width == 600
        assert image.height > 500



def test_shared_schedule_change_predicate_and_photo_sender():
    from bot import schedule_snapshot_changed, send_schedule_change_photo

    current = {"2026-11-21": "0" * 48}
    unchanged_previous = {"2026-11-21": "0" * 48}
    changed_previous = {"2026-11-21": "1" + "0" * 47}

    assert schedule_snapshot_changed(unchanged_previous, current) is False
    assert schedule_snapshot_changed(changed_previous, current) is True
    assert schedule_snapshot_changed({}, current) is False

    class FakeTelegram:
        def __init__(self):
            self.sent = []
        def send_photo(self, chat_id, png, caption):
            self.sent.append((chat_id, png, caption))
            return 4321

    tg = FakeTelegram()
    message_id = send_schedule_change_photo(tg, "12345", b"test-png", "test-caption")
    assert message_id == 4321
    assert tg.sent == [("12345", b"test-png", "test-caption")]



def test_telegram_error_detects_permanently_unavailable_chats():
    from telegram_api import TelegramError

    assert TelegramError(403, "Forbidden: user is deactivated").permanent_chat_failure
    assert TelegramError(403, "Forbidden: bot was blocked by the user").permanent_chat_failure
    assert not TelegramError(429, "Too Many Requests").permanent_chat_failure
    assert not TelegramError(500, "Internal Server Error").permanent_chat_failure


def test_background_refresh_disables_deactivated_telegram_chat(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    import bot as bot_module
    from telegram_api import TelegramError

    class FakeTelegram:
        def send_photo(self, *args):
            raise TelegramError(403, "Forbidden: user is deactivated")

    class FakeStore:
        def save(self, state):
            pass

    tz = ZoneInfo("Europe/Kyiv")
    day = datetime.now(tz).date().isoformat()
    current = {day: {"slots": "1" + "0" * 47, "created": "changed"}}
    monkeypatch.setattr(bot_module, "fetch_schedule", lambda *a, **k: current)
    monkeypatch.setattr(bot_module, "render_schedule_png", lambda *a, **k: b"png")
    state = {"users": {"deactivated-chat": {
        "active": True,
        "address_key": "22016/40637/24",
        "group": "5.1",
        "address_label": "Test address",
        "snapshot": {day: "0" * 48},
        "message_id": None,
        "fail_count": 0,
        "error_notified": False,
    }}}

    stats = bot_module.process_subscriptions(
        FakeTelegram(), state, SimpleNamespace(), tz, object(), FakeStore()
    )

    assert state["users"]["deactivated-chat"]["active"] is False
    assert stats["permanent_chat_failures"] == 1
    assert stats["send_failed"] == 0
    assert stats["sent"] == 0


def test_address_directory_diff_skips_unchanged_rows_and_only_updates_changed():
    from sync_address_index import COLUMNS, diff_records, prepare_records

    source = [{
        "id": 8950,
        "city": {"id": 22016, "name": "Велика Лука"},
        "street": {"id": 40637, "name": "вул. Івана Франка"},
        "buildingNames": "1, 10, 24",
        "chergGpv": "5.1",
    }]
    prepared, digest = prepare_records(source)
    existing = [dict(zip(COLUMNS, prepared[0]))]

    changed, removed, unchanged = diff_records(prepared, existing)
    assert digest
    assert changed == []
    assert removed == []
    assert unchanged == 1

    changed_source = [{
        **source[0],
        "buildingNames": "1, 10, 24, 25",
    }]
    changed_prepared, changed_digest = prepare_records(changed_source)
    changed_rows, removed_rows, unchanged_rows = diff_records(changed_prepared, existing)
    assert changed_digest != digest
    assert len(changed_rows) == 1
    assert removed_rows == []
    assert unchanged_rows == 0
