import datetime as dt
import re
import unicodedata

from al_backend.discord_author_mappings import apply_discord_author_mappings
from al_backend.repository import (
    Repository,
    _add_break_interval_to_buckets,
    _apply_breaks_to_hourly_activity,
    _date_query,
    _empty_event_deltas,
    _empty_hourly_activity,
    _interval_deltas,
    _merge_batch_deltas,
    _normalize_telegram_username,
    _plugin_day_seconds,
    _saved_prefab_delta,
    _with_activity_mix,
    _with_alerts,
    _with_productivity,
)
from al_backend.telegram_bot import (
    BotConfig,
    edit_reminder_message,
    format_prompt_time,
    format_duration_label,
    get_updates,
    handle_callback_query,
    parse_callback_data,
    parse_event_type,
    parse_reminder_callback,
    meeting_summary_chat_id,
    send_break_activity_prompt_message,
    send_online_prompt_message,
    send_plain_message,
    send_reminder_message,
    telegram_username,
)


class FakeCursor(list):
    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self


class FakeCollection:
    def __init__(self):
        self.items = []

    def find_one(self, query, projection=None, sort=None):
        for item in self.items:
            if self._matches(item, query):
                return item.copy()

        return None

    def find(self, query=None, projection=None):
        query = query or {}
        return FakeCursor([item.copy() for item in self.items if self._matches(item, query)])

    def insert_one(self, item):
        self.items.append(item.copy())

        class Result:
            inserted_id = item.get("_id")

        return Result()

    def update_one(self, query, operation, upsert=False):
        for item in self.items:
            if self._matches(item, query):
                self._apply_operation(item, operation, inserting=False)
                return

        if upsert:
            item = query.copy()
            self._apply_operation(item, operation, inserting=True)
            self.items.append(item)

    def update_many(self, query, operation):
        for item in self.items:
            if self._matches(item, query):
                self._apply_operation(item, operation, inserting=False)

    def delete_one(self, query):
        before = len(self.items)
        self.items = [item for item in self.items if not self._matches(item, query)]

        class Result:
            def __init__(self, deleted_count):
                self.deleted_count = deleted_count

        return Result(before - len(self.items))

    def find_one_and_delete(self, query):
        for index, item in enumerate(self.items):
            if self._matches(item, query):
                return self.items.pop(index).copy()

        return None

    def delete_many(self, query):
        matching = [item for item in self.items if self._matches(item, query)]
        self.items = [item for item in self.items if not self._matches(item, query)]

        class Result:
            def __init__(self, deleted_count):
                self.deleted_count = deleted_count

        return Result(len(matching))

    def distinct(self, key):
        return sorted({item.get(key) for item in self.items if item.get(key)})

    def count_documents(self, query):
        return len([item for item in self.items if self._matches(item, query)])

    def _matches(self, item, query):
        for key, value in query.items():
            item_value = item.get(key)

            if isinstance(value, dict):
                if "$in" in value and item_value not in value["$in"]:
                    return False

                if "$nin" in value and item_value in value["$nin"]:
                    return False

                if "$regex" in value and not re.search(value["$regex"], str(item_value or "")):
                    return False

                if item_value is None and any(operator in value for operator in ("$gte", "$lte", "$lt", "$gt")):
                    return False

                if "$gte" in value and item_value < value["$gte"]:
                    return False

                if "$lte" in value and item_value > value["$lte"]:
                    return False

                if "$lt" in value and item_value >= value["$lt"]:
                    return False

                if "$gt" in value and item_value <= value["$gt"]:
                    return False
            elif item_value != value:
                return False

        return True

    def _apply_operation(self, item, operation, inserting):
        for key, value in operation.get("$set", {}).items():
            item[key] = value

        if inserting:
            for key, value in operation.get("$setOnInsert", {}).items():
                item.setdefault(key, value)

        for key, value in operation.get("$inc", {}).items():
            item[key] = item.get(key, 0) + value

        for key in operation.get("$unset", {}).keys():
            item.pop(key, None)


class FakeDb:
    def __init__(self):
        self.author_profiles = FakeCollection()
        self.author_aliases = FakeCollection()
        self.activity_snapshots = FakeCollection()
        self.aggregate_metadata = FakeCollection()
        self.break_events = FakeCollection()
        self.break_sessions = FakeCollection()
        self.day_sessions = FakeCollection()
        self.telegram_day_reminders = FakeCollection()
        self.telegram_online_prompts = FakeCollection()
        self.telegram_break_activity_prompts = FakeCollection()
        self.telegram_meeting_auto_afk_notifications = FakeCollection()
        self.meeting_recordings = FakeCollection()
        self.meeting_summaries = FakeCollection()
        self.break_intervals = FakeCollection()
        self.meeting_events = FakeCollection()
        self.meeting_sessions = FakeCollection()
        self.meeting_intervals = FakeCollection()
        self.calendar_marks = FakeCollection()
        self.daily_author_activity = FakeCollection()
        self.aggregate_session_state = FakeCollection()
        self.interval_settings = FakeCollection()
        self.manual_report_expectations = FakeCollection()
        self.raw_activity_events = FakeCollection()
        self.raw_event_batches = FakeCollection()
        self.raw_reports = FakeCollection()
        self.report_challenges = FakeCollection()
        self.report_refresh_requests = FakeCollection()
        self.report_rows = FakeCollection()
        self.report_security_events = FakeCollection()
        self.site_users = FakeCollection()
        self.system_settings = FakeCollection()


def fake_repository():
    repo = Repository.__new__(Repository)
    repo.db = FakeDb()
    repo.default_send_interval_seconds = 60
    return repo


def test_productivity_ignores_first_break_hour():
    author = _with_productivity({"activeSeconds": 5 * 3600, "idleSeconds": 3 * 3600, "breakSeconds": 60 * 60})

    assert author["productivity"] == 62.5


def test_productivity_penalizes_break_time_after_first_hour():
    author = _with_productivity({"activeSeconds": 5 * 3600, "idleSeconds": 3 * 3600, "breakSeconds": 80 * 60})

    assert author["productivity"] == 60


def test_productivity_exceeds_one_hundred_with_overtime():
    author = _with_productivity(
        {
            "activeSeconds": 8 * 3600,
            "idleSeconds": 0,
            "breakSeconds": 0,
            "overtimeActiveSeconds": 2 * 3600,
        }
    )

    assert author["productivity"] == 125.0


def test_date_query_uses_inclusive_range():
    assert _date_query("2026-04-01", "2026-04-30") == {"date": {"$gte": "2026-04-01", "$lte": "2026-04-30"}}


def test_telegram_username_is_normalized_for_mapping():
    assert _normalize_telegram_username(" @Dmitry_Shane ") == "dmitry_shane"


def test_telegram_bot_parses_team_commands():
    assert parse_event_type(" онлайн ") == "online"
    assert parse_event_type("ONLINE") == "online"
    assert parse_event_type("АФК") == "afk"
    assert parse_event_type("афк!") == "afk"
    assert parse_event_type("оффлайн") == "offline"
    assert parse_event_type("hello") is None


def test_telegram_bot_uses_sender_username():
    assert telegram_username({"username": "@Dmitry_Shane"}) == "dmitry_shane"


def test_telegram_bot_parses_reminder_callbacks():
    assert parse_reminder_callback("altd:abc123:offline") == ("abc123", "offline")
    assert parse_reminder_callback("altd:abc123:overtime") == ("abc123", "overtime")
    assert parse_reminder_callback("altd:abc123:afk") is None
    assert parse_reminder_callback("altm:abc:confirm_online") is None
    assert parse_reminder_callback("hello") is None


def test_telegram_bot_parses_online_prompt_callbacks():
    assert parse_callback_data("altm:abc123:confirm_online") == ("altm", "abc123", "confirm_online")
    assert parse_callback_data("altm:abc123:dismiss") == ("altm", "abc123", "dismiss")
    assert parse_callback_data("altm:abc:offline") is None
    assert parse_callback_data("altd:x:offline") == ("altd", "x", "offline")
    assert parse_callback_data("altb:break1:confirm_online") == ("altb", "break1", "confirm_online")
    assert parse_callback_data("altb:break1:still_afk") == ("altb", "break1", "still_afk")
    assert parse_callback_data("altb:break1:dismiss") is None


def test_telegram_bot_update_polling_includes_callbacks(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": []}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)

    assert get_updates("token", None) == []
    assert captured["method"] == "getUpdates"
    assert "callback_query" in captured["params"]["allowed_updates"]


def test_telegram_bot_reminder_message_mentions_author_and_has_buttons(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": {"message_id": 42}}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)
    result = send_reminder_message("token", 123, "Hi @dmitryshane. Did you forget to go offline, or are you working overtime?", "reminder-1")

    assert result["result"]["message_id"] == 42
    assert captured["method"] == "sendMessage"
    assert "@dmitryshane" in captured["params"]["text"]
    assert '"Offline"' in captured["params"]["reply_markup"]
    assert '"Overtime"' in captured["params"]["reply_markup"]
    assert "altd:reminder-1:offline" in captured["params"]["reply_markup"]
    assert "altd:reminder-1:overtime" in captured["params"]["reply_markup"]


def test_telegram_bot_edit_message_names_author(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)

    edit_reminder_message("token", 123, 42, "overtime", "dmitryshane")

    assert captured["method"] == "editMessageText"
    assert captured["params"]["text"] == "Done. @dmitryshane Telegram day closed as Overtime."
    assert '"inline_keyboard": []' in captured["params"]["reply_markup"]


def test_telegram_bot_online_prompt_message_has_buttons(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": {"message_id": 99}}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)
    send_online_prompt_message("token", 123, "Hi @user. Test?", "prompt-1")

    assert captured["method"] == "sendMessage"
    assert "altm:prompt-1:confirm_online" in captured["params"]["reply_markup"]
    assert "altm:prompt-1:dismiss" in captured["params"]["reply_markup"]
    assert "I'm online" in captured["params"]["reply_markup"]


def test_telegram_bot_break_activity_prompt_message_has_buttons(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": {"message_id": 100}}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)
    send_break_activity_prompt_message("token", 123, "Hi @user. Test?", "prompt-1")

    assert captured["method"] == "sendMessage"
    assert "altb:prompt-1:confirm_online" in captured["params"]["reply_markup"]
    assert "altb:prompt-1:still_afk" in captured["params"]["reply_markup"]
    assert "I'm online" in captured["params"]["reply_markup"]
    assert "Still AFK" in captured["params"]["reply_markup"]


def test_telegram_bot_plain_message_has_no_buttons(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": {"message_id": 101}}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)
    send_plain_message("token", 123, "Hello")

    assert captured["method"] == "sendMessage"
    assert captured["params"] == {"chat_id": 123, "text": "Hello"}


def test_telegram_bot_formats_prompt_time_in_author_time_zone():
    assert format_prompt_time("2026-05-01T13:09:24", "Europe/Madrid") == "15:09"


def test_telegram_bot_formats_duration_labels():
    assert format_duration_label(60) == "1 minute"
    assert format_duration_label(600) == "10 minutes"
    assert format_duration_label(75) == "75 seconds"


def test_telegram_bot_callback_edits_online_prompt(monkeypatch):
    calls = []
    config = BotConfig(
        token="token",
        backend_url="https://activity.mempic.com",
        allowed_chat_id=123,
        bot_secret="secret",
    )

    def fake_close_reminder(backend_url, bot_secret, reminder_id, action, actor_telegram_username="", *, reminder_kind="day_end"):
        calls.append(("close", reminder_id, action, reminder_kind))
        return {"ok": True}

    def fake_edit_reminder_message(token, chat_id, message_id, action, telegram_username="", *, reminder_kind="day_end"):
        calls.append(("edit", action, reminder_kind))
        return {"ok": True}

    def fake_answer_callback_query(token, callback_query_id, text):
        calls.append(("answer", text))
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.close_reminder", fake_close_reminder)
    monkeypatch.setattr("al_backend.telegram_bot.edit_reminder_message", fake_edit_reminder_message)
    monkeypatch.setattr("al_backend.telegram_bot.answer_callback_query", fake_answer_callback_query)

    handle_callback_query(
        config,
        {
            "id": "callback-1",
            "data": "altm:prompt-1:dismiss",
            "from": {"username": "dmitryshane"},
            "message": {
                "message_id": 42,
                "chat": {"id": 123},
                "text": 'Hi @dmitryshane. You have activity today but no "online" message yet.',
            },
        },
    )

    assert ("close", "prompt-1", "dismiss", "online_prompt") in calls
    assert ("edit", "dismiss", "online_prompt") in calls
    assert ("answer", "Dismissed.") in calls


def test_telegram_bot_callback_edits_break_activity_prompt(monkeypatch):
    calls = []
    config = BotConfig(
        token="token",
        backend_url="https://activity.mempic.com",
        allowed_chat_id=123,
        bot_secret="secret",
    )

    def fake_close_reminder(backend_url, bot_secret, reminder_id, action, actor_telegram_username="", *, reminder_kind="day_end"):
        calls.append(("close", reminder_id, action, reminder_kind))
        return {"ok": True}

    def fake_edit_reminder_message(token, chat_id, message_id, action, telegram_username="", *, reminder_kind="day_end"):
        calls.append(("edit", action, reminder_kind))
        return {"ok": True}

    def fake_answer_callback_query(token, callback_query_id, text):
        calls.append(("answer", text))
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.close_reminder", fake_close_reminder)
    monkeypatch.setattr("al_backend.telegram_bot.edit_reminder_message", fake_edit_reminder_message)
    monkeypatch.setattr("al_backend.telegram_bot.answer_callback_query", fake_answer_callback_query)

    handle_callback_query(
        config,
        {
            "id": "callback-1",
            "data": "altb:prompt-1:still_afk",
            "from": {"username": "dmitryshane"},
            "message": {
                "message_id": 42,
                "chat": {"id": 123},
                "text": "Hi @dmitryshane. You went AFK at 14:30, but I now see activity from you.",
            },
        },
    )

    assert ("close", "prompt-1", "still_afk", "break_activity_prompt") in calls
    assert ("edit", "still_afk", "break_activity_prompt") in calls
    assert ("answer", "Still AFK.") in calls


def test_telegram_bot_callback_edits_message_after_close(monkeypatch):
    calls = []
    config = BotConfig(
        token="token",
        backend_url="https://activity.mempic.com",
        allowed_chat_id=123,
        bot_secret="secret",
    )

    def fake_close_reminder(backend_url, bot_secret, reminder_id, action, actor_telegram_username="", *, reminder_kind="day_end"):
        calls.append(("close", backend_url, bot_secret, reminder_id, action, actor_telegram_username, reminder_kind))
        return {"ok": True}

    def fake_edit_reminder_message(token, chat_id, message_id, action, telegram_username="", *, reminder_kind="day_end"):
        calls.append(("edit", token, chat_id, message_id, action, telegram_username, reminder_kind))
        return {"ok": True}

    def fake_answer_callback_query(token, callback_query_id, text):
        calls.append(("answer", token, callback_query_id, text))
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.close_reminder", fake_close_reminder)
    monkeypatch.setattr("al_backend.telegram_bot.edit_reminder_message", fake_edit_reminder_message)
    monkeypatch.setattr("al_backend.telegram_bot.answer_callback_query", fake_answer_callback_query)

    handle_callback_query(
        config,
        {
            "id": "callback-1",
            "data": "altd:reminder-1:overtime",
            "from": {"username": "dmitryshane"},
            "message": {
                "message_id": 42,
                "chat": {"id": 123},
                "text": "Hi @dmitryshane. Did you forget to go offline, or are you working overtime?",
            },
        },
    )

    assert ("close", "https://activity.mempic.com", "secret", "reminder-1", "overtime", "dmitryshane", "day_end") in calls
    assert ("edit", "token", 123, 42, "overtime", "dmitryshane", "day_end") in calls
    assert ("answer", "token", "callback-1", "Telegram day closed.") in calls


def test_telegram_bot_callback_rejects_wrong_user(monkeypatch):
    calls = []
    config = BotConfig(
        token="token",
        backend_url="https://activity.mempic.com",
        allowed_chat_id=123,
        bot_secret="secret",
    )

    def fake_close_reminder(*args):
        calls.append(("close", *args))
        return {"ok": True}

    def fake_edit_reminder_message(*args):
        calls.append(("edit", *args))
        return {"ok": True}

    def fake_answer_callback_query(token, callback_query_id, text):
        calls.append(("answer", token, callback_query_id, text))
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.close_reminder", fake_close_reminder)
    monkeypatch.setattr("al_backend.telegram_bot.edit_reminder_message", fake_edit_reminder_message)
    monkeypatch.setattr("al_backend.telegram_bot.answer_callback_query", fake_answer_callback_query)

    handle_callback_query(
        config,
        {
            "id": "callback-1",
            "data": "altd:reminder-1:offline",
            "from": {"username": "someone_else"},
            "message": {
                "message_id": 42,
                "chat": {"id": 123},
                "text": "Hi @dmitryshane. Did you forget to go offline, or are you working overtime?",
            },
        },
    )

    assert calls == [("answer", "token", "callback-1", "Sorry, this reminder was not sent to you.")]


def test_security_alerts_are_scoped_to_resolved_authors():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats"})
    repo.upsert_author_alias("D Shane", "Dmitry Shane")
    repo.db.daily_author_activity.insert_one({"source": "ual", "author": "Dmitry Shane", "projectId": "unity", "date": "2026-04-29"})
    repo.db.daily_author_activity.insert_one({"source": "ual", "author": "Igor Mats", "projectId": "unity", "date": "2026-04-29"})
    repo.log_report_security_event(
        "report_forgery_attempt",
        "ual",
        author="D Shane",
        device_id="device-dmitry",
        challenge_id="challenge-dmitry",
        message="Dmitry alert",
    )
    repo.log_report_security_event(
        "report_forgery_attempt",
        "ual",
        author="Igor Mats",
        device_id="device-igor",
        challenge_id="challenge-igor",
        message="Igor alert",
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    authors = {author["rawAuthor"]: author for author in summary["authors"]}

    assert [alert["message"] for alert in authors["Dmitry Shane"]["alerts"] if alert["type"] == "report_forgery_attempt"] == ["Dmitry alert"]
    assert [alert["message"] for alert in authors["Igor Mats"]["alerts"] if alert["type"] == "report_forgery_attempt"] == ["Igor alert"]


def test_repeated_security_alerts_keep_distinct_ids():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.daily_author_activity.insert_one({"source": "ual", "author": "Dmitry Shane", "projectId": "unity", "date": "2026-04-29"})
    repo.log_report_security_event("report_forgery_attempt", "ual", author="Dmitry Shane", device_id="device-a", challenge_id="challenge-a")
    repo.log_report_security_event("report_forgery_attempt", "ual", author="Dmitry Shane", device_id="device-b", challenge_id="challenge-b")

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry Shane")
    security_alerts = [alert for alert in author["alerts"] if alert["type"] == "report_forgery_attempt"]

    assert len(security_alerts) == 2
    assert len({alert["id"] for alert in security_alerts}) == 2


def test_authors_without_alerts_have_zero_alert_stats():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Healthy Author", "displayName": "Healthy Author"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Healthy Author",
            "projectId": "unity",
            "date": "2026-04-29",
            "lastReceivedAt": dt.datetime.now(dt.UTC),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Healthy Author")

    assert author["alertStats"]["total"] == 0
    assert author["alerts"] == []


def test_manual_profile_is_listed_before_activity_and_can_receive_email():
    repo = fake_repository()

    repo.upsert_author_profile(
        raw_author="Future Artist",
        display_name="Future Artist",
        team="Art",
        telegram_username="@future_artist",
        plugin_enabled=True,
        author_color="#13a37b",
        time_zone_id="UTC",
    )

    assert repo.list_authors() == ["Future Artist"]
    assert repo.author_profiles()[0]["telegramUsername"] == "future_artist"

    repo.update_author_email("Future Artist", "future@example.com")

    assert repo.author_profiles()[0]["authorEmail"] == "future@example.com"


def test_cyrillic_author_names_are_unicode_normalized_for_profile_matching():
    repo = fake_repository()
    decomposed_author = "Але\u0308на Иванова"
    composed_author = unicodedata.normalize("NFC", decomposed_author)

    repo.upsert_author_profile(
        raw_author=decomposed_author,
        display_name=None,
        team="Art",
        telegram_username="@alena",
        plugin_enabled=True,
        author_color="#13a37b",
        time_zone_id="UTC",
    )
    repo.update_author_email(composed_author, "alena@example.com")

    assert repo.list_authors() == [composed_author]
    assert repo.author_profiles()[0]["rawAuthor"] == composed_author
    assert repo.author_profiles()[0]["authorEmail"] == "alena@example.com"


def test_bootstrap_admin_does_not_reset_existing_password():
    repo = fake_repository()

    repo.ensure_bootstrap_site_admin("admin@example.com", "first-password")
    repo.ensure_bootstrap_site_admin("admin@example.com", "second-password")

    assert repo.authenticate_site_user("admin@example.com", "first-password")
    assert repo.authenticate_site_user("admin@example.com", "second-password") is None


def test_telegram_online_creates_visible_report_row_and_live_day_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})

    result = repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    authors = {}
    totals = {"daySeconds": 0, "telegramDaySeconds": 0, "breakSeconds": 0}
    repo._apply_live_telegram_summary(
        authors,
        {},
        totals,
        repo._profiles_by_raw_author(),
        {},
        {},
        "2026-04-28",
        "2026-04-28",
        None,
        dt.datetime(2026, 4, 28, 9, 10, tzinfo=dt.UTC),
    )

    assert result["status"] == "online_recorded"
    assert repo.db.report_rows.items[0]["source"] == "telegram"
    assert repo.db.report_rows.items[0]["reportType"] == "telegram"
    assert repo.db.report_rows.items[0]["telegramEventType"] == "online"
    assert authors["Future Artist"]["telegramDaySeconds"] == 10 * 60


def test_activity_summary_exposes_raw_plugin_day_time_without_changing_effective_plugin_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 120,
            "idleSeconds": 600,
            "workWindowSeconds": 32400,
            "hourlyActivity": [
                {"hour": 10, "activeSeconds": 120, "idleSeconds": 600, "breakSeconds": 0, "overtimeActiveSeconds": 0}
            ],
        }
    )
    repo.db.break_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 28, 10, 5, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 28, 10, 10, tzinfo=dt.UTC),
            "date": "2026-04-28",
            "timeZoneId": "UTC",
            "breakSeconds": 300,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["rawPluginDaySeconds"] == 720
    assert author["pluginDaySeconds"] == 420


def test_activity_summary_exposes_telegram_to_first_activity_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T09:17:30Z",
            "receivedAt": dt.datetime(2026, 4, 28, 9, 17, 31, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["telegramToFirstActivitySeconds"] == 17 * 60 + 30


def test_activity_summary_marks_visual_missed_time_before_online_hour_without_affecting_totals():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:17:30Z")
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[9]["missedSeconds"] == 17 * 60 + 30
    assert hourly_by_hour[9]["missedStartSeconds"] == 17 * 60 + 30
    assert hourly_by_hour[9]["missedEndSeconds"] == 0
    assert hourly_by_hour[9]["idleSeconds"] == 0
    assert author["idleSeconds"] == 0
    assert author["pluginDaySeconds"] == 60


def test_activity_summary_marks_visual_missed_time_after_offline_hour_without_affecting_totals():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T19:20:00Z")
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[19]["missedSeconds"] == 40 * 60
    assert hourly_by_hour[19]["missedStartSeconds"] == 0
    assert hourly_by_hour[19]["missedEndSeconds"] == 40 * 60
    assert hourly_by_hour[19]["idleSeconds"] == 0
    assert author["idleSeconds"] == 0
    assert author["pluginDaySeconds"] == 60


def test_activity_summary_marks_visual_missed_time_after_latest_plugin_report():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T19:20:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T21:37:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 21, 37, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[19]["missedEndSeconds"] == 0
    assert hourly_by_hour[21]["missedSeconds"] == 23 * 60
    assert hourly_by_hour[21]["missedEndSeconds"] == 23 * 60
    assert author["telegramToFirstActivitySeconds"] == 12 * 3600 + 37 * 60


def test_activity_summary_does_not_mark_visual_end_missed_before_offline():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T14:37:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 14, 37, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[14]["missedEndSeconds"] == 0
    assert hourly_by_hour[14]["missedSeconds"] == 0


def test_activity_summary_counts_latest_report_to_offline_gap_as_idle():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T20:10:00Z")
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "activity-start",
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 28, 9, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T20:07:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 20, 7, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 7 * 60,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    hourly_activity = _empty_hourly_activity()
    hourly_activity[20]["idleSeconds"] = 7 * 60
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 0,
            "idleSeconds": 7 * 60,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[20]["idleSeconds"] == 10 * 60
    assert hourly_by_hour[20]["missedEndSeconds"] == 50 * 60
    assert hourly_by_hour[20]["missedSeconds"] == 50 * 60
    assert author["idleSeconds"] == 10 * 60
    assert author["pluginDaySeconds"] == 10 * 60
    assert summary["totals"]["idleSeconds"] == 10 * 60
    assert summary["totals"]["pluginDaySeconds"] == 10 * 60


def test_activity_summary_counts_unaccounted_latest_report_hour_gap_as_idle():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T20:10:00Z")
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "activity-start",
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 28, 9, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T20:07:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 20, 7, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 6 * 60,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    hourly_activity = _empty_hourly_activity()
    hourly_activity[20]["idleSeconds"] = 6 * 60
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 0,
            "idleSeconds": 6 * 60,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[20]["idleSeconds"] == 10 * 60
    assert hourly_by_hour[20]["missedEndSeconds"] == 50 * 60
    assert author["idleSeconds"] == 10 * 60
    assert author["pluginDaySeconds"] == 10 * 60
    assert summary["totals"]["idleSeconds"] == 10 * 60
    assert summary["totals"]["pluginDaySeconds"] == 10 * 60


def test_telegram_to_first_activity_gap_counts_as_idle_hourly_activity():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T10:17:30Z",
            "receivedAt": dt.datetime(2026, 4, 28, 10, 17, 31, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert author["telegramToFirstActivitySeconds"] == 77 * 60 + 30
    assert author["idleSeconds"] == 77 * 60 + 30
    assert author["pluginDaySeconds"] == 78 * 60 + 30
    assert author["rawPluginDaySeconds"] == 78 * 60 + 30
    assert author["productivity"] == 1.27
    assert hourly_by_hour[9]["idleSeconds"] == 3600
    assert hourly_by_hour[10]["idleSeconds"] == 17 * 60 + 30


def test_telegram_to_first_activity_uses_first_raw_activity_event_before_report_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry",
            "displayName": "Dmitriy Zhdamarov",
            "telegramUsername": "zhdamarovich",
            "timeZoneId": "Europe/Madrid",
        }
    )
    repo.record_break_event("zhdamarovich", "online", "2026-04-30T07:35:42Z")
    repo.db.raw_activity_events.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "eventType": "focus",
            "occurredAtUtc": "2026-04-30T07:36:12Z",
            "occurredAtLocal": "2026-04-30T09:36:12+02:00",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:43:12+02:00",
            "receivedAt": dt.datetime(2026, 4, 30, 7, 46, 15, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
            "savedPrefabDeltas": [{"path": "Assets/Level.prefab", "name": "Level", "saveCount": 1}],
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:45:13+02:00",
            "receivedAt": dt.datetime(2026, 4, 30, 7, 46, 15, tzinfo=dt.UTC),
            "activeDeltaSeconds": 120,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "projectId": "unity",
            "date": "2026-04-30",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-30", end_date="2026-04-30")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry")

    assert author["telegramToFirstActivitySeconds"] == 30


def test_telegram_to_first_activity_falls_back_to_first_positive_report_row_without_raw_events():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry",
            "displayName": "Dmitriy Zhdamarov",
            "telegramUsername": "zhdamarovich",
            "timeZoneId": "Europe/Madrid",
        }
    )
    repo.record_break_event("zhdamarovich", "online", "2026-04-30T07:35:42Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:43:12+02:00",
            "receivedAt": dt.datetime(2026, 4, 30, 7, 46, 15, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
            "savedPrefabDeltas": [{"path": "Assets/Level.prefab", "name": "Level", "saveCount": 1}],
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:45:13+02:00",
            "receivedAt": dt.datetime(2026, 4, 30, 7, 46, 15, tzinfo=dt.UTC),
            "activeDeltaSeconds": 120,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "projectId": "unity",
            "date": "2026-04-30",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-30", end_date="2026-04-30")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry")

    assert author["telegramToFirstActivitySeconds"] == 9 * 60 + 31


def _insert_presence_daily_activity(repo, received_at):
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "lastReceivedAt": received_at,
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [{"type": "selection", "count": 1}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": _empty_hourly_activity(),
        }
    )


def _author_from_summary(repo, now):
    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28", now=now)
    return next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")


def _author_status(repo, now):
    return _author_from_summary(repo, now)["status"]


def test_fresh_normal_plugin_report_without_telegram_offline_keeps_author_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC))

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 1, tzinfo=dt.UTC))
    assert author["status"] == "online"
    assert "stalePresence" not in author


def test_telegram_offline_after_fresh_plugin_report_marks_author_stale():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 6, tzinfo=dt.UTC))
    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"


def test_stale_presence_reports_when_unity_reports_stop_without_telegram_signoff():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.UTC))

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 30, tzinfo=dt.UTC))
    assert author["status"] == "stale"
    assert author["stalePresence"] == "reports"


def test_with_alerts_stale_presence_telegram_without_reports_stopped():
    frozen_now = dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC)
    author = {
        "rawAuthor": "Future Artist",
        "displayName": "Future Artist",
        "securityAlerts": [],
        "telegramAlerts": [],
        "activeSeconds": 60,
        "idleSeconds": 0,
        "breakSeconds": 0,
        "overtimeActiveSeconds": 0,
        "activityCounts": [{"type": "selection", "count": 1}],
        "lastReceivedAt": dt.datetime(2026, 4, 28, 18, 9, tzinfo=dt.UTC),
    }
    wrapped = _with_activity_mix(_with_productivity(author))
    result = _with_alerts(
        wrapped,
        60,
        frozen_now,
        {"offlineAt": dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC)},
    )
    assert result["status"] == "stale"
    assert result["stalePresence"] == "telegram"


def test_with_alerts_stale_presence_both_when_telegram_offline_and_reports_stopped():
    frozen_now = dt.datetime(2026, 4, 28, 18, 30, tzinfo=dt.UTC)
    author = {
        "rawAuthor": "Future Artist",
        "displayName": "Future Artist",
        "securityAlerts": [],
        "telegramAlerts": [],
        "activeSeconds": 60,
        "idleSeconds": 0,
        "breakSeconds": 0,
        "overtimeActiveSeconds": 0,
        "activityCounts": [{"type": "selection", "count": 1}],
        "lastReceivedAt": dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.UTC),
    }
    wrapped = _with_activity_mix(_with_productivity(author))
    result = _with_alerts(
        wrapped,
        60,
        frozen_now,
        {"offlineAt": dt.datetime(2026, 4, 28, 18, 5, tzinfo=dt.UTC)},
    )
    assert result["status"] == "stale"
    assert result["stalePresence"] == "both"


def test_non_overtime_report_after_telegram_offline_keeps_author_stale():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 0,
            "overtimeActiveDeltaMicroseconds": 0,
        }
    )

    assert _author_status(repo, dt.datetime(2026, 4, 28, 18, 11, tzinfo=dt.UTC)) == "stale"


def test_fresh_overtime_report_after_telegram_offline_marks_author_online_temporarily():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 30,
            "overtimeActiveDeltaMicroseconds": 30 * 1_000_000,
        }
    )

    assert _author_status(repo, dt.datetime(2026, 4, 28, 18, 11, tzinfo=dt.UTC)) == "online"


def test_overtime_report_after_telegram_offline_expires_with_stale_threshold():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 30,
            "overtimeActiveDeltaMicroseconds": 30 * 1_000_000,
        }
    )

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 13, 1, tzinfo=dt.UTC))
    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"


def test_heartbeat_idle_after_telegram_offline_is_suppressed_from_aggregates():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T18:00:00Z",
            "occurredAtLocal": "2026-04-28T18:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC),
        }
    )
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")

    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-04-28T18:10:00Z",
            "occurredAtLocal": "2026-04-28T18:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
        }
    )

    assert deltas["idleDeltaSeconds"] == 0
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})
    assert daily["idleSeconds"] == 0


def test_rebuild_suppresses_stored_heartbeat_idle_after_telegram_offline():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "activity-1",
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-28T18:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "heartbeat-1",
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "heartbeat",
            "occurredAtUtc": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-28T18:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
        }
    )

    repo.rebuild_aggregates_if_needed(force=True)

    assert not [
        row
        for row in repo.db.report_rows.items
        if row.get("source") == "ual" and row.get("idleDeltaSeconds", 0) > 0
    ]
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})
    assert daily["idleSeconds"] == 0


def test_overtime_activity_after_telegram_offline_is_allowed():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 32400,
            "activeMicroseconds": 32400 * 1_000_000,
            "idleSeconds": 0,
            "idleMicroseconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": _empty_hourly_activity(),
        }
    )
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T18:00:00Z",
            "occurredAtLocal": "2026-04-28T18:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC),
        }
    )
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:00:15Z")

    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T18:00:30Z",
            "occurredAtLocal": "2026-04-28T18:00:30+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 0, 30, tzinfo=dt.UTC),
        }
    )

    assert deltas["overtimeActiveDeltaSeconds"] == 30
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})
    assert daily["overtimeActiveSeconds"] == 30


def test_telegram_online_after_offline_restores_normal_presence():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.record_break_event("future_artist", "online", "2026-04-28T18:10:00Z")

    assert _author_status(repo, dt.datetime(2026, 4, 28, 18, 11, tzinfo=dt.UTC)) == "online"


def test_discord_meeting_reduces_idle_for_any_activity_source():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "discordUserId": "123",
            "timeZoneId": "UTC",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-29",
            "activeSeconds": 0,
            "idleSeconds": 3600,
            "workWindowSeconds": 32400,
            "hourlyActivity": [
                {"hour": 10, "activeSeconds": 0, "idleSeconds": 3600, "breakSeconds": 0, "overtimeActiveSeconds": 0}
            ],
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 15, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 45, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
            "meetingSeconds": 1800,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 11, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert author["idleSeconds"] == 1800
    assert author["meetingSeconds"] == 1800
    assert author["productivity"] == 0
    assert hour["idleSeconds"] == 1800
    assert hour["meetingSeconds"] == 1800


def test_discord_meeting_does_not_replace_active_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-29",
            "activeSeconds": 600,
            "idleSeconds": 600,
            "workWindowSeconds": 32400,
            "hourlyActivity": [
                {"hour": 10, "activeSeconds": 600, "idleSeconds": 600, "breakSeconds": 0, "overtimeActiveSeconds": 0}
            ],
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
            "meetingSeconds": 1200,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 11, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert author["activeSeconds"] == 600
    assert author["idleSeconds"] == 0
    assert author["meetingSeconds"] == 1200
    assert hour["activeSeconds"] == 600
    assert hour["idleSeconds"] == 0
    assert hour["meetingSeconds"] == 1200


def test_discord_meeting_overlay_is_not_applied_twice_across_sources():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})

    for source in ("first-plugin", "second-plugin"):
        repo.db.daily_author_activity.insert_one(
            {
                "source": source,
                "author": "Future Artist",
                "projectId": source,
                "date": "2026-04-29",
                "activeSeconds": 0,
                "idleSeconds": 3600,
                "workWindowSeconds": 32400,
                "hourlyActivity": [
                    {"hour": 10, "activeSeconds": 0, "idleSeconds": 3600, "breakSeconds": 0, "overtimeActiveSeconds": 0}
                ],
            }
        )

    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 30, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
            "meetingSeconds": 1800,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 11, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["meetingSeconds"] == 1800
    assert author["idleSeconds"] == 5400


def test_live_discord_meeting_session_is_included_in_summary():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.meeting_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert author["meetingSeconds"] == 1200
    assert hour["meetingSeconds"] == 1200


def test_live_discord_meeting_session_is_counted_with_empty_daily_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-29",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": [],
        }
    )
    repo.db.meeting_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert author["meetingSeconds"] == 1200
    assert summary["totals"]["meetingSeconds"] == 1200
    assert hour["meetingSeconds"] == 1200


def test_live_discord_closed_meeting_interval_is_counted_without_daily_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "Europe/Madrid"})
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 28, 22, 29, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 28, 23, 26, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "Europe/Madrid",
            "meetingSeconds": 3420,
        }
    )

    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 4, 29, 1, 0, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")["hourlyActivity"]
    hour_0 = next(item for item in hourly if item["hour"] == 0)
    hour_1 = next(item for item in hourly if item["hour"] == 1)

    assert author["meetingSeconds"] == 3420
    assert summary["totals"]["meetingSeconds"] == 3420
    assert hour_0["meetingSeconds"] == 31 * 60
    assert hour_1["meetingSeconds"] == 26 * 60


def test_live_discord_cross_midnight_meeting_fills_selected_local_day_hours():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "Europe/Madrid"})
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 30, 21, 46, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 30, 22, 29, tzinfo=dt.UTC),
            "date": "2026-04-30",
            "timeZoneId": "Europe/Madrid",
            "meetingSeconds": 43 * 60,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 30, 22, 29, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 30, 23, 26, tzinfo=dt.UTC),
            "date": "2026-05-01",
            "timeZoneId": "Europe/Madrid",
            "meetingSeconds": 57 * 60,
        }
    )

    summary = repo.activity_summary(
        start_date="2026-05-01",
        end_date="2026-05-01",
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 5, 1, 1, 0, tzinfo=dt.UTC),
    )
    hourly = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")["hourlyActivity"]
    hour_0 = next(item for item in hourly if item["hour"] == 0)
    hour_1 = next(item for item in hourly if item["hour"] == 1)

    assert hour_0["meetingSeconds"] == 3600
    assert hour_1["meetingSeconds"] == 26 * 60


def test_live_discord_meeting_session_marks_offline_author_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.break_events.insert_one(
        {
            "rawAuthor": "Future Artist",
            "eventType": "offline",
            "timestamp": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.meeting_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["status"] == "online"
    assert "stalePresence" not in author
    assert author["meetingSeconds"] == 1200


def test_live_discord_meeting_session_compensates_idle_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-29",
            "activeSeconds": 600,
            "idleSeconds": 1500,
            "workWindowSeconds": 32400,
            "hourlyActivity": [
                {"hour": 10, "activeSeconds": 600, "idleSeconds": 1500, "breakSeconds": 0, "overtimeActiveSeconds": 0}
            ],
        }
    )
    repo.db.meeting_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 10, 15, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert author["activeSeconds"] == 600
    assert author["idleSeconds"] == 600
    assert author["meetingSeconds"] == 900
    assert hour["activeSeconds"] == 600
    assert hour["idleSeconds"] == 600
    assert hour["meetingSeconds"] == 900
    assert summary["totals"]["idleSeconds"] == 600


def test_discord_meeting_graph_uses_full_interval_bucket_over_active_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Евгений Доценко",
            "displayName": "Evgeniy Dotsenko",
            "discordUserId": "123",
            "timeZoneId": "Europe/Sofia",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Евгений Доценко",
            "projectId": "bike-rush-2",
            "date": "2026-05-01",
            "activeSeconds": 2597,
            "idleSeconds": 1003,
            "hourlyActivity": [
                {"hour": 17, "activeSeconds": 2597, "idleSeconds": 1003},
                {"hour": 18, "activeSeconds": 0, "idleSeconds": 396},
            ],
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Евгений Доценко",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 5, 1, 14, 2, 50, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 1, 14, 11, 5, tzinfo=dt.UTC),
            "date": "2026-05-01",
            "timeZoneId": "Europe/Sofia",
            "meetingSeconds": 495,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Евгений Доценко",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 5, 1, 14, 27, 58, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 1, 15, 3, 48, tzinfo=dt.UTC),
            "date": "2026-05-01",
            "timeZoneId": "Europe/Sofia",
            "meetingSeconds": 2150,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01", now=dt.datetime(2026, 5, 1, 15, 30, tzinfo=dt.UTC))
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Евгений Доценко")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Евгений Доценко")["hourlyActivity"]
    hour_17 = next(item for item in hourly if item["hour"] == 17)
    hour_18 = next(item for item in hourly if item["hour"] == 18)

    assert hour_17["meetingSeconds"] == 2417
    assert hour_17["activeSeconds"] == 1183
    assert hour_17["idleSeconds"] == 0
    assert hour_18["meetingSeconds"] == 228
    assert author["activeSeconds"] == 2597
    assert author["meetingSeconds"] == 2645


def test_discord_voice_events_open_and_close_meeting_session():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})

    join = repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")
    leave = repo.record_discord_voice_event("123", "future", "leave", timestamp="2026-04-29T10:25:00+00:00")

    assert join["status"] == "meeting_started"
    assert leave["status"] == "meeting_closed"
    assert leave["meetingSeconds"] == 1500
    assert repo.db.meeting_sessions.items == []
    assert repo.db.meeting_intervals.items[0]["meetingSeconds"] == 1500
    assert repo.db.report_rows.items[-1]["source"] == "discord"
    assert repo.db.report_rows.items[-1]["reportType"] == "meeting"


def test_discord_auto_afk_closes_meeting_at_solo_start_and_schedules_notification():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "discordUserId": "123",
            "timeZoneId": "UTC",
        }
    )
    repo.record_discord_voice_event("123", "future", "join", guild_id="guild", channel_id="meeting", timestamp="2026-04-29T10:00:00+00:00")

    result = repo.record_discord_meeting_auto_afk(
        "123",
        "future",
        guild_id="guild",
        meeting_channel_id="meeting",
        afk_channel_id="afk",
        solo_started_at="2026-04-29T10:25:00+00:00",
        moved_at="2026-04-29T10:35:00+00:00",
        threshold_seconds=60,
    )

    assert result["status"] == "meeting_auto_afk"
    assert result["meetingSeconds"] == 1500
    assert repo.db.meeting_sessions.items == []
    assert repo.db.meeting_intervals.items[0]["endedAt"] == dt.datetime(2026, 4, 29, 10, 25, tzinfo=dt.UTC)
    assert repo.db.meeting_intervals.items[0]["meetingSeconds"] == 1500
    assert repo.db.meeting_events.items[-1]["eventType"] == "auto_afk"
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["telegramUsername"] == "future_artist"
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["excludedSeconds"] == 600
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["thresholdSeconds"] == 60


def test_discord_settings_default_and_save():
    repo = fake_repository()

    assert repo.get_discord_settings()["meetingAutoAfkTimeoutSeconds"] == 600

    result = repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=900,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=2,
        meeting_summary_min_duration_seconds=120,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
    )

    assert result["meetingAutoAfkTimeoutSeconds"] == 900
    assert result["meetingSummariesEnabled"] is True
    assert repo.get_discord_settings()["meetingAutoAfkTimeoutSeconds"] == 900


def test_meeting_recording_finished_creates_summary_notification():
    repo = fake_repository()
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=2,
        meeting_summary_min_duration_seconds=60,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
    )

    class FakeSummary:
        transcript = "Dmitry and Igor agreed to create a Discord summary task."
        summary = "Participants:\n- Dmitry\n- Igor\n\nDecisions:\n- Add Discord summaries.\n\nAction items:\n- Create a task.\n\nOpen questions:\n- None."

    result = repo.process_meeting_recording_finished(
        recording_id="recording-1",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:03:00+00:00",
        participant_discord_user_ids=["1", "2"],
        participant_names=["Dmitry", "Igor"],
        audio_path="/tmp/missing.wav",
        summary_generator=lambda path, people, language: FakeSummary(),
    )
    notifications = repo.claim_due_telegram_meeting_summary_notifications()

    assert result["status"] == "summary_created"
    assert notifications[0]["summaryId"] == result["summaryId"]
    assert "Discord summaries" in notifications[0]["summary"]


def test_meeting_recording_finished_skips_solo_recording():
    repo = fake_repository()
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=2,
        meeting_summary_min_duration_seconds=60,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
    )

    result = repo.process_meeting_recording_finished(
        recording_id="recording-1",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:03:00+00:00",
        participant_discord_user_ids=["1"],
        participant_names=["Dmitry"],
        audio_path="/tmp/missing.wav",
        summary_generator=lambda path, people, language: None,
    )

    assert result["status"] == "skipped_not_enough_participants"
    assert repo.db.meeting_summaries.items == []


def test_discord_auto_afk_is_idempotent():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "discordUserId": "123",
            "timeZoneId": "UTC",
        }
    )
    repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")

    first = repo.record_discord_meeting_auto_afk(
        "123",
        "future",
        solo_started_at="2026-04-29T10:25:00+00:00",
        moved_at="2026-04-29T10:35:00+00:00",
    )
    second = repo.record_discord_meeting_auto_afk(
        "123",
        "future",
        solo_started_at="2026-04-29T10:25:00+00:00",
        moved_at="2026-04-29T10:35:30+00:00",
    )

    assert first["status"] == "meeting_auto_afk"
    assert second["status"] == "auto_afk_already_recorded"
    assert len(repo.db.meeting_intervals.items) == 1
    assert len(repo.db.telegram_meeting_auto_afk_notifications.items) == 1


def test_telegram_meeting_auto_afk_notifications_can_be_claimed_and_marked_sent():
    repo = fake_repository()
    repo.db.telegram_meeting_auto_afk_notifications.insert_one(
        {
            "reminderId": "notification-1",
            "autoAfkEventId": "123:2026-04-29T10:25:00+00:00",
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "soloStartedAt": dt.datetime(2026, 4, 29, 10, 25, tzinfo=dt.UTC),
            "movedAt": dt.datetime(2026, 4, 29, 10, 35, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "excludedSeconds": 600,
            "thresholdSeconds": 60,
            "status": "pending",
        }
    )

    notifications = repo.claim_due_telegram_meeting_auto_afk_notifications(now=dt.datetime(2026, 4, 29, 10, 36, tzinfo=dt.UTC))
    sent = repo.mark_telegram_meeting_auto_afk_notification_sent("notification-1", 42)

    assert notifications[0]["reminderId"] == "notification-1"
    assert notifications[0]["telegramUsername"] == "future_artist"
    assert notifications[0]["excludedSeconds"] == 600
    assert notifications[0]["thresholdSeconds"] == 60
    assert sent == {"ok": True}
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["status"] == "sent"
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["messageId"] == 42


def test_telegram_private_chat_is_saved_for_profile():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitryshane"})

    result = repo.save_telegram_private_chat("dmitryshane", 12345)
    profile = repo.db.author_profiles.find_one({"rawAuthor": "Dmitry Shane"})

    assert result["ok"] is True
    assert profile["telegramPrivateChatId"] == 12345
    assert repo.author_profiles()[0]["telegramPrivateChatId"] == 12345


def test_meeting_summary_chat_id_uses_private_recipient():
    assert meeting_summary_chat_id(1, {"recipient": {"kind": "private", "chatId": 42}}) == 42
    assert meeting_summary_chat_id(1, {"recipient": {"kind": "work_chat"}}) == 1


def test_discord_author_mappings_update_known_telegram_profiles_only():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Evgeniy Dotsenko", "displayName": "Evgeniy Dotsenko", "telegramUsername": "ama_deus"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "telegramUsername": "igormats"})

    result = apply_discord_author_mappings(repo)
    evgeniy = repo.db.author_profiles.find_one({"telegramUsername": "ama_deus"})
    igor = repo.db.author_profiles.find_one({"telegramUsername": "igormats"})

    assert evgeniy["discordUserId"] == "645196366494171139"
    assert evgeniy["discordUsername"] == "Evgeniy Dotsenko"
    assert igor["discordUserId"] == "689526024857321504"
    assert igor["discordUsername"] == "Igor Mats"
    assert [item["telegramUsername"] for item in result["updated"]] == ["ama_deus", "igormats"]
    assert "dmitryshane" in result["missingTelegramUsernames"]
    assert "vedamir_infinum" in result["missingTelegramUsernames"]
    assert "zhdamarovich" in result["missingTelegramUsernames"]


def test_idle_only_reports_during_discord_meeting_are_hidden_from_latest_reports():
    repo = fake_repository()
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 10, tzinfo=dt.UTC),
            "idleDeltaSeconds": 300,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "discord",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "reportType": "meeting",
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 30, tzinfo=dt.UTC),
        }
    )

    reports = repo.latest_reports(start_date="2026-04-29", end_date="2026-04-29")

    assert [report["source"] for report in reports] == ["discord"]


def test_active_reports_during_discord_meeting_remain_visible_in_latest_reports():
    repo = fake_repository()
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 10, tzinfo=dt.UTC),
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 300,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 30, tzinfo=dt.UTC),
        }
    )

    reports = repo.latest_reports(start_date="2026-04-29", end_date="2026-04-29")

    assert [report["source"] for report in reports] == ["future-plugin"]


def test_plugin_reports_during_break_interval_are_hidden_from_latest_reports():
    repo = fake_repository()
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 10, tzinfo=dt.UTC),
            "idleDeltaSeconds": 300,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:15:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 15, tzinfo=dt.UTC),
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 60,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "telegram",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "reportType": "telegram",
            "telegramEventType": "afk",
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "discord",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:20:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC),
            "reportType": "meeting",
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.break_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 30, tzinfo=dt.UTC),
        }
    )

    reports = repo.latest_reports(start_date="2026-04-29", end_date="2026-04-29")

    assert {report["source"] for report in reports} == {"discord", "telegram"}


def test_idle_only_reports_during_open_break_are_hidden_from_latest_reports():
    repo = fake_repository()
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 10, tzinfo=dt.UTC),
            "idleDeltaSeconds": 300,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.break_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    reports = repo.latest_reports(start_date="2026-04-29", end_date="2026-04-29")

    assert reports == []


def test_reports_page_paginates_author_reports_and_keeps_source_options():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})

    for index, source in enumerate(["ual", "bal", "ual"]):
        repo.db.report_rows.insert_one(
            {
                "source": source,
                "author": "Future Artist",
                "date": "2026-04-29",
                "recordedAt": f"2026-04-29T10:0{index}:00+00:00",
                "receivedAt": dt.datetime(2026, 4, 29, 10, index, tzinfo=dt.UTC),
                "activeDeltaSeconds": 60,
                "idleDeltaSeconds": 0,
                "overtimeActiveDeltaSeconds": 0,
            }
        )

    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Other Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:05:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )

    first_page = repo.reports_page(
        start_date="2026-04-29",
        end_date="2026-04-29",
        author="Future Artist",
        limit=2,
        offset=0,
    )
    filtered_page = repo.reports_page(
        start_date="2026-04-29",
        end_date="2026-04-29",
        author="Future Artist",
        source="ual",
        limit=10,
        offset=0,
    )

    assert first_page["total"] == 3
    assert len(first_page["reports"]) == 2
    assert first_page["sources"] == ["bal", "ual"]
    assert filtered_page["total"] == 2
    assert [report["source"] for report in filtered_page["reports"]] == ["ual", "ual"]
    assert filtered_page["sources"] == ["bal", "ual"]


def test_cursor_activity_project_appears_in_saved_files_without_file_save():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "date": "2026-04-29",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [{"type": "scene_changed", "count": 3}],
            "savedPrefabs": [],
            "hourlyActivity": _empty_hourly_activity(),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "bike-rush-2",
            "date": "2026-04-29",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "activityCounts": [{"type": "selection", "count": 1}],
            "savedPrefabs": [{"path": "Assets/Project/Levels/Level.008/Level.008.prefab", "name": "Level.008", "saveCount": 1}],
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert {"path": "cursor:AL", "name": "AL", "projectId": "AL", "saveCount": 3} in author["savedPrefabs"]
    assert {
        "path": "Assets/Project/Levels/Level.008/Level.008.prefab",
        "name": "Level.008",
        "saveCount": 1,
    } in author["savedPrefabs"]


def test_activity_summary_author_source_uses_latest_report_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "fch",
            "author": "Future Artist",
            "projectId": "figma",
            "date": "2026-04-29",
            "lastRecordedAt": "2026-04-29T09:00:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "fch",
            "pluginVersion": "figma-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeDeltaSeconds": 120,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "discord",
            "pluginVersion": "discord-bot",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "reportType": "meeting",
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["source"] == "discord"
    assert author["pluginVersion"] == "discord-bot"
    assert author["lastRecordedAt"] == "2026-04-29T10:00:00+00:00"


def test_open_telegram_day_is_capped_at_ten_hours_and_alerted():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})

    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    summary = repo.activity_summary(
        start_date="2026-04-28",
        end_date="2026-04-28",
        now=dt.datetime(2026, 4, 28, 19, 30, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["telegramDaySeconds"] == 10 * 3600
    assert summary["totals"]["telegramDaySeconds"] == 10 * 3600
    assert any(alert["type"] == "telegram_day_open" for alert in author["alerts"])


def test_due_telegram_reminder_includes_profile_username_and_deduplicates_after_sent():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")

    reminders = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))

    assert len(reminders) == 1
    assert reminders[0]["telegramUsername"] == "future_artist"
    assert reminders[0]["rawAuthor"] == "Future Artist"

    repo.mark_telegram_day_reminder_sent(reminders[0]["reminderId"], 42)

    assert repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 5, tzinfo=dt.UTC)) == []


def test_telegram_reminder_offline_closes_day_at_click_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z")

    assert result["status"] == "reminder_offline"
    assert result["daySeconds"] == 10 * 3600 + 15 * 60
    assert repo.db.day_sessions.items[0]["lastOfflineAt"] == dt.datetime(2026, 4, 28, 19, 15, tzinfo=dt.UTC)
    assert repo.db.daily_author_activity.items[0]["source"] == "telegram"
    assert repo.db.daily_author_activity.items[0]["daySeconds"] == 10 * 3600 + 15 * 60
    assert repo.db.report_rows.items[-1]["telegramEventType"] == "offline"
    assert repo.db.report_rows.items[-1]["telegramStatus"] == "reminder_offline"
    assert repo.db.report_rows.items[-1]["metadata"]["reminderAction"] == "offline"


def test_telegram_reminder_overtime_closes_day_with_overtime_metadata():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-28T19:30:00Z")

    assert result["status"] == "reminder_overtime"
    assert result["daySeconds"] == 10 * 3600 + 30 * 60
    assert repo.db.report_rows.items[-1]["telegramStatus"] == "reminder_overtime"
    assert repo.db.report_rows.items[-1]["metadata"]["reminderAction"] == "overtime"


def test_activity_after_telegram_overtime_reminder_counts_as_overtime():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]
    repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-28T19:30:00Z")

    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T19:31:00Z",
            "occurredAtLocal": "2026-04-28T19:31:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 31, tzinfo=dt.UTC),
        }
    )
    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T19:31:30Z",
            "occurredAtLocal": "2026-04-28T19:31:30+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 31, 30, tzinfo=dt.UTC),
        }
    )
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})

    assert deltas["overtimeActiveDeltaSeconds"] == 30
    assert daily["overtimeActiveSeconds"] == 30
    assert daily["activeSeconds"] == 0


def test_activity_before_telegram_overtime_reminder_stays_normal_during_rebuild():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]
    repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-28T19:30:00Z")

    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T19:20:00Z",
            "occurredAtLocal": "2026-04-28T19:20:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 20, tzinfo=dt.UTC),
        }
    )
    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T19:20:30Z",
            "occurredAtLocal": "2026-04-28T19:20:30+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 20, 30, tzinfo=dt.UTC),
        }
    )
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})

    assert deltas["activeDeltaSeconds"] == 30
    assert deltas["overtimeActiveDeltaSeconds"] == 0
    assert daily["activeSeconds"] == 30
    assert daily["overtimeActiveSeconds"] == 0


def test_telegram_reminder_close_closes_open_break_session():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "afk", "2026-04-28T18:45:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z")

    assert result["breakSeconds"] == 30 * 60
    assert repo.db.break_sessions.items == []
    assert repo.db.break_intervals.items[0]["breakSeconds"] == 30 * 60


def test_telegram_reminder_close_is_idempotent():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    first = repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z")
    second = repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-28T19:20:00Z")

    reminder_reports = [row for row in repo.db.report_rows.items if row.get("telegramStatus", "").startswith("reminder_")]
    assert first["status"] == "reminder_offline"
    assert second["status"] == "reminder_offline_already_closed"
    assert len(reminder_reports) == 1
    assert repo.db.day_sessions.items[0]["daySeconds"] == 10 * 3600 + 15 * 60


def test_telegram_reminder_close_rejects_wrong_actor():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z", "other_person")

    reminder_reports = [row for row in repo.db.report_rows.items if row.get("telegramStatus", "").startswith("reminder_")]
    assert result["status"] == "wrong_user"
    assert "lastOfflineAt" not in repo.db.day_sessions.items[0]
    assert reminder_reports == []


def test_telegram_reminder_close_keeps_day_visible_on_close_date():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "telegramUsername": "igormats",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.record_break_event("igormats", "online", "2026-04-29T00:35:33Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 29, 23, 25, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-29T23:29:37Z")
    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 23, 30, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Igor Mats")

    assert result["daySeconds"] == 82444
    assert repo.db.day_sessions.items[0]["date"] == "2026-04-29"
    assert repo.db.daily_author_activity.items[0]["date"] == "2026-04-29"
    assert author["telegramDaySeconds"] == 82444


def test_telegram_online_prompt_schedules_once_per_day():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)

    assert len(repo.db.telegram_online_prompts.items) == 1


def test_telegram_online_prompt_not_scheduled_without_telegram():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "timeZoneId": "UTC"})
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC))

    assert repo.db.telegram_online_prompts.items == []


def test_telegram_online_prompt_not_scheduled_when_day_session_exists():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.day_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": t0, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)

    assert repo.db.telegram_online_prompts.items == []


def test_telegram_online_prompt_claim_after_delay():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)

    assert repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=14)) == []

    due = repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=16))
    assert len(due) == 1
    assert repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=20)) == []


def test_telegram_online_prompt_superseded_when_day_session_exists_at_claim():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    repo.db.day_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": t0, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )

    assert repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=16)) == []

    doc = repo.db.telegram_online_prompts.items[0]
    assert doc["status"] == "closed"
    assert doc["closeAction"] == "superseded_day_session"


def test_telegram_online_prompt_dismiss_closes_without_online_event():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    rid = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_online_prompt(rid, "dismiss", "2026-04-30T12:00:00Z", "ta")

    assert result["ok"]
    assert result["status"] == "online_prompt_dismissed"
    assert repo.db.day_sessions.items == []
    assert [e for e in repo.db.break_events.items if e.get("eventType") == "online"] == []


def test_telegram_online_prompt_confirm_records_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    rid = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"
    before = len(repo.db.break_events.items)

    result = repo.close_telegram_online_prompt(rid, "confirm_online", "2026-04-30T12:00:00Z", "ta")

    assert result["ok"]
    assert len(repo.db.break_events.items) > before
    assert repo.db.day_sessions.items
    assert repo.db.telegram_online_prompts.items[0]["closeAction"] == "confirm_online"


def test_record_break_event_online_invalidates_online_prompt():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    repo.record_break_event("ta", "online", "2026-04-30T09:00:00Z")

    doc = repo.db.telegram_online_prompts.items[0]
    assert doc["status"] == "closed"
    assert doc["closeAction"] == "cancelled_by_online"


def test_telegram_online_prompt_close_rejects_wrong_actor():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    rid = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_online_prompt(rid, "dismiss", "2026-04-30T12:00:00Z", "other")

    assert result["status"] == "wrong_user"


def test_break_activity_prompt_schedules_after_sixty_minutes_only():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )

    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=59))
    assert repo.db.telegram_break_activity_prompts.items == []

    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=61))

    assert len(repo.db.telegram_break_activity_prompts.items) == 1
    assert repo.db.telegram_break_activity_prompts.items[0]["status"] == "pending"


def test_break_activity_prompt_claim_and_mark_sent():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))

    due = repo.claim_due_telegram_break_activity_prompts(started_at + dt.timedelta(minutes=61))

    assert len(due) == 1
    assert due[0]["telegramUsername"] == "ta"
    assert repo.claim_due_telegram_break_activity_prompts(started_at + dt.timedelta(minutes=62)) == []

    repo.mark_telegram_break_activity_prompt_sent(due[0]["reminderId"], 44)

    assert repo.db.telegram_break_activity_prompts.items[0]["status"] == "sent"
    assert repo.db.telegram_break_activity_prompts.items[0]["messageId"] == 44


def test_break_activity_prompt_confirm_online_closes_break():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))
    reminder_id = repo.db.telegram_break_activity_prompts.items[0]["reminderId"]
    repo.db.telegram_break_activity_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_break_activity_prompt(reminder_id, "confirm_online", "2026-04-30T09:05:00Z", "ta")

    assert result["ok"]
    assert result["status"] == "break_activity_prompt_confirmed_online"
    assert repo.db.break_sessions.items == []
    assert repo.db.break_intervals.items[0]["breakSeconds"] == 65 * 60
    assert repo.db.telegram_break_activity_prompts.items[0]["closeAction"] == "confirm_online"


def test_break_activity_prompt_still_afk_keeps_break_and_reports_hidden():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "A",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:05:00Z",
            "receivedAt": dt.datetime(2026, 4, 30, 9, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 300,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))
    reminder_id = repo.db.telegram_break_activity_prompts.items[0]["reminderId"]
    repo.db.telegram_break_activity_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_break_activity_prompt(reminder_id, "still_afk", "2026-04-30T09:05:00Z", "ta")

    assert result["status"] == "break_activity_prompt_still_afk"
    assert repo.db.break_sessions.items
    assert repo.latest_reports(start_date="2026-04-30", end_date="2026-04-30") == []


def test_break_activity_prompt_close_rejects_wrong_actor():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))
    reminder_id = repo.db.telegram_break_activity_prompts.items[0]["reminderId"]
    repo.db.telegram_break_activity_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_break_activity_prompt(reminder_id, "confirm_online", "2026-04-30T09:05:00Z", "other")

    assert result["status"] == "wrong_user"
    assert repo.db.break_sessions.items


def test_telegram_online_uses_author_time_zone_for_date():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "telegramUsername": "igormats", "timeZoneId": "UTC"})

    repo.record_break_event("igormats", "online", "2026-04-29T00:06:17+02:00")

    assert repo.db.day_sessions.items[0]["date"] == "2026-04-28"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-28"


def test_telegram_online_uses_madrid_author_time_zone_for_date():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "telegramUsername": "dmitryshane", "timeZoneId": "Europe/Madrid"}
    )

    repo.record_break_event("dmitryshane", "online", "2026-04-29T00:06:17+02:00")

    assert repo.db.day_sessions.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-29"


def test_author_time_zone_update_rebuckets_existing_telegram_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "telegramUsername": "dmitryshane"})

    repo.record_break_event("dmitryshane", "online", "2026-04-29T00:06:17+02:00")

    assert repo.db.day_sessions.items[0]["date"] == "2026-04-28"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-28"

    repo.update_author_time_zone("Dmitry Shane", "Europe/Madrid", "CEST")

    assert repo.db.author_profiles.items[0]["timeZoneId"] == "Europe/Madrid"
    assert repo.db.break_events.items[0]["date"] == "2026-04-29"
    assert repo.db.day_sessions.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["timeZoneId"] == "Europe/Madrid"
    assert repo.db.report_rows.items[0]["timeZoneDisplayName"] == "CEST"


def test_windows_time_zone_update_rebuckets_existing_telegram_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Evgeniy Dotsenko", "displayName": "Evgeniy Dotsenko", "telegramUsername": "ama_deus"})

    repo.record_break_event("ama_deus", "online", "2026-04-29T21:30:00Z")

    assert repo.db.day_sessions.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["timeZoneId"] == "UTC"

    repo.update_author_time_zone("Evgeniy Dotsenko", "FLE Standard Time", "(UTC+02:00) Sofia")

    assert repo.db.author_profiles.items[0]["timeZoneId"] == "Europe/Sofia"
    assert repo.db.author_profiles.items[0]["timeZoneDisplayName"] == "(UTC+02:00) Sofia"
    assert repo.db.break_events.items[0]["date"] == "2026-04-30"
    assert repo.db.break_events.items[0]["timeZoneId"] == "Europe/Sofia"
    assert repo.db.day_sessions.items[0]["date"] == "2026-04-30"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-30"
    assert repo.db.report_rows.items[0]["timeZoneId"] == "Europe/Sofia"
    assert repo.db.report_rows.items[0]["timeZoneDisplayName"] == "(UTC+02:00) Sofia"


def test_configured_author_time_zone_overrides_windows_time_zone():
    repo = fake_repository()

    repo.update_author_time_zone("Denis Ostrovskiy", "FLE Daylight Time", "FLE Daylight Time")

    assert repo.db.author_profiles.items[0]["timeZoneId"] == "Europe/Kyiv"
    assert repo.db.author_profiles.items[0]["timeZoneDisplayName"] == "FLE Daylight Time"


def test_configured_author_time_zone_normalizes_saved_report_row():
    repo = fake_repository()
    payload = {
        "author": "Denis Ostrovskiy",
        "authorEmail": "denis@example.com",
        "projectId": "project",
        "sessionId": "session",
        "deviceId": "device",
        "timeZoneId": "FLE Daylight Time",
        "timeZoneDisplayName": "FLE Daylight Time",
        "events": [
            {
                "eventId": "event-1",
                "eventType": "scene_changed",
                "occurredAtUtc": "2026-04-29T15:58:07Z",
                "occurredAtLocal": "2026-04-29T18:58:07+03:00",
                "metadata": {"inputType": "LEFTMOUSE"},
                },
                {
                    "eventId": "event-2",
                    "eventType": "scene_changed",
                    "occurredAtUtc": "2026-04-29T15:59:07Z",
                    "occurredAtLocal": "2026-04-29T18:59:07+03:00",
                    "metadata": {"inputType": "LEFTMOUSE"},
            }
        ],
    }

    repo.save_report("bal", "0.1.0", "packet", payload, "challenge")

    assert repo.db.author_profiles.items[0]["timeZoneId"] == "Europe/Kyiv"
    assert repo.db.report_rows.items[0]["timeZoneId"] == "Europe/Kyiv"


def test_author_local_today_summary_includes_authors_on_different_local_dates():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Madrid Author", "displayName": "Madrid Author", "timeZoneId": "Europe/Madrid"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Utc Author", "displayName": "Utc Author", "timeZoneId": "UTC"})
    repo.db.author_profiles.insert_one({"rawAuthor": "No Activity Author", "displayName": "No Activity Author", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Madrid Author",
            "date": "2026-04-29",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "timeZoneId": "Europe/Madrid",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Utc Author",
            "date": "2026-04-28",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 4, 28, 22, 30, tzinfo=dt.UTC),
    )

    authors = {author["rawAuthor"]: author for author in summary["authors"]}
    assert authors["Madrid Author"]["activeSeconds"] == 60
    assert authors["Utc Author"]["activeSeconds"] == 120
    assert authors["No Activity Author"]["activeSeconds"] == 0
    assert authors["No Activity Author"]["status"] == "stale"


def test_activity_summary_regular_date_keeps_calendar_filter_and_all_authors():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Madrid Author", "displayName": "Madrid Author", "timeZoneId": "Europe/Madrid"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Utc Author", "displayName": "Utc Author", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Madrid Author",
            "date": "2026-04-29",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "timeZoneId": "Europe/Madrid",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Utc Author",
            "date": "2026-04-28",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        now=dt.datetime(2026, 4, 28, 22, 30, tzinfo=dt.UTC),
    )

    authors = {author["rawAuthor"]: author for author in summary["authors"]}
    assert authors["Madrid Author"]["activeSeconds"] == 60
    assert authors["Utc Author"]["activeSeconds"] == 0
    assert authors["Utc Author"]["status"] == "stale"


def test_live_telegram_summary_includes_open_session_outside_selected_date_range():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "telegramUsername": "igormats", "timeZoneId": "UTC"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "telegramUsername": "igormats",
            "date": "2026-04-28",
            "startedAt": dt.datetime(2026, 4, 28, 22, 6, tzinfo=dt.UTC),
            "daySeconds": 0,
        }
    )
    authors = {}
    totals = {"daySeconds": 0, "telegramDaySeconds": 0, "breakSeconds": 0}

    repo._apply_live_telegram_summary(
        authors,
        {},
        totals,
        repo._profiles_by_raw_author(),
        {},
        {},
        "2026-04-29",
        "2026-04-29",
        "authorLocalToday",
        dt.datetime(2026, 4, 28, 22, 16, tzinfo=dt.UTC),
    )

    assert authors["Igor Mats"]["telegramDaySeconds"] == 10 * 60
    assert totals["telegramDaySeconds"] == 10 * 60


def test_activity_summary_keeps_mix_and_saved_files_per_author():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Dmitry Shane",
            "date": "2026-04-29",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "activityCounts": [{"type": "selection", "count": 3}],
            "savedPrefabs": [{"path": "Assets/Dmitry.prefab", "name": "Dmitry", "saveCount": 2}],
            "hourlyActivity": _empty_hourly_activity(),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Igor Mats",
            "date": "2026-04-29",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [{"type": "play_mode", "count": 5}],
            "savedPrefabs": [{"path": "Assets/Igor.prefab", "name": "Igor", "saveCount": 4}],
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    authors = {author["rawAuthor"]: author for author in summary["authors"]}

    assert authors["Dmitry Shane"]["activityMix"] == [{"type": "selection", "count": 3, "percent": 100}]
    assert authors["Dmitry Shane"]["savedPrefabs"] == [{"path": "Assets/Dmitry.prefab", "name": "Dmitry", "saveCount": 2}]
    assert authors["Igor Mats"]["activityMix"] == [{"type": "play_mode", "count": 5, "percent": 100}]
    assert authors["Igor Mats"]["savedPrefabs"] == [{"path": "Assets/Igor.prefab", "name": "Igor", "saveCount": 4}]


def test_analytics_summary_includes_day_hourly_activity():
    repo = fake_repository()
    today = dt.date.today()
    hourly_activity = _empty_hourly_activity()
    hourly_activity[10]["activeSeconds"] = 1800
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Dmitry Shane",
            "date": today.isoformat(),
            "activeSeconds": 1800,
            "idleSeconds": 900,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.analytics_summary()
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Dmitry Shane")
    month = next(item for item in author["months"] if item["month"] == today.month)
    day = next(
        item
        for week in month["weeks"]
        for item in week["days"]
        if item["date"] == today.isoformat()
    )
    empty_day = next(
        item
        for week in month["weeks"]
        for item in week["days"]
        if item["date"] != today.isoformat()
    )

    assert len(day["hourlyActivity"]) == 24
    assert day["hourlyActivity"][10]["activeSeconds"] == 1800
    assert len(empty_day["hourlyActivity"]) == 24
    assert empty_day["hourlyActivity"] == _empty_hourly_activity()


def test_overtime_activity_summary_splits_mix_and_saved_files():
    repo = fake_repository()
    base_event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "authorEmail": "dmitry@example.com",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-29",
        "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
    }
    normal_activity = {
        **base_event,
        "eventType": "selection",
        "occurredAtUtc": "2026-04-29T10:00:00Z",
        "occurredAtLocal": "2026-04-29T10:00:00+00:00",
    }
    normal_save = {
        **base_event,
        "eventType": "prefab_saved",
        "occurredAtUtc": "2026-04-29T10:00:10Z",
        "occurredAtLocal": "2026-04-29T10:00:10+00:00",
        "metadata": {"path": "Assets/Normal.prefab", "name": "Normal"},
    }

    repo._apply_raw_event_to_aggregates(normal_activity)
    repo._apply_raw_event_to_aggregates(normal_save)
    repo.db.daily_author_activity.insert_one(
        {
            "source": "seed",
            "projectId": "seed",
            "author": "Dmitry Shane",
            "date": "2026-04-29",
            "activeSeconds": 9 * 3600,
            "idleSeconds": 0,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    overtime_activity = {
        **base_event,
        "eventType": "play_mode",
        "occurredAtUtc": "2026-04-29T19:00:00Z",
        "occurredAtLocal": "2026-04-29T19:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 29, 19, 0, tzinfo=dt.UTC),
    }
    overtime_save = {
        **base_event,
        "eventType": "prefab_saved",
        "occurredAtUtc": "2026-04-29T19:00:10Z",
        "occurredAtLocal": "2026-04-29T19:00:10+00:00",
        "receivedAt": dt.datetime(2026, 4, 29, 19, 0, 10, tzinfo=dt.UTC),
        "metadata": {"path": "Assets/Overtime.prefab", "name": "Overtime"},
    }

    repo._apply_raw_event_to_aggregates(overtime_activity)
    repo._apply_raw_event_to_aggregates(overtime_save)

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry Shane")

    assert author["activityMix"] == [
        {"type": "select", "count": 1, "percent": 50},
        {"type": "prefab_saved", "count": 1, "percent": 50},
    ]
    assert author["savedPrefabs"] == [{"path": "Assets/Normal.prefab", "name": "Normal", "saveCount": 1}]
    assert author["overtimeActivityMix"] == [
        {"type": "play_mode", "count": 1, "percent": 50},
        {"type": "prefab_saved", "count": 1, "percent": 50},
    ]
    assert author["overtimeSavedPrefabs"] == [{"path": "Assets/Overtime.prefab", "name": "Overtime", "saveCount": 1}]
    assert summary["overtimeActivityMix"] == [
        {"type": "play_mode", "count": 1, "percent": 50},
        {"type": "prefab_saved", "count": 1, "percent": 50},
    ]


def test_activity_summary_returns_empty_hourly_activity_for_telegram_only_author():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "telegramUsername": "igormats", "timeZoneId": "UTC"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "telegramUsername": "igormats",
            "date": "2026-04-29",
            "startedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "daySeconds": 0,
        }
    )

    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        now=dt.datetime(2026, 4, 29, 9, 10, tzinfo=dt.UTC),
    )

    hourly_by_author = {author["rawAuthor"]: author for author in summary["hourlyActivityByAuthor"]}
    assert len(hourly_by_author["Igor Mats"]["hourlyActivity"]) == 24
    assert hourly_by_author["Igor Mats"]["hourlyActivity"] == _empty_hourly_activity()


def test_delete_author_data_preserves_profile_but_delete_profile_removes_it():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "telegramUsername": "future_artist"})
    repo.db.report_rows.insert_one({"author": "Future Artist"})
    repo.db.day_sessions.insert_one({"rawAuthor": "Future Artist", "telegramUsername": "future_artist", "date": "2026-04-28"})

    data_result = repo.delete_author_data("Future Artist")

    assert data_result["ok"] is True
    assert repo.db.author_profiles.items == [{"rawAuthor": "Future Artist", "telegramUsername": "future_artist"}]

    repo.db.report_rows.insert_one({"author": "Future Artist"})
    profile_result = repo.delete_author_profile("Future Artist")

    assert profile_result["ok"] is True
    assert repo.db.author_profiles.items == []
    assert repo.db.report_rows.items == []


def test_break_event_flow_records_day_and_break():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane"})
    repo.db.daily_author_activity.insert_one({"author": "Dmitry Shane", "date": "2026-04-28", "breakSeconds": 0})

    assert repo.record_break_event("dmitry_shane", "online", "2026-04-28T09:00:00Z")["status"] == "online_recorded"
    assert repo.record_break_event("dmitry_shane", "afk", "2026-04-28T12:00:00Z")["status"] == "break_started"
    break_closed = repo.record_break_event("dmitry_shane", "online", "2026-04-28T12:15:00Z")
    day_closed = repo.record_break_event("dmitry_shane", "offline", "2026-04-28T18:00:00Z")

    assert break_closed == {"ok": True, "status": "break_closed", "breakSeconds": 15 * 60}
    assert day_closed["status"] == "day_closed"
    assert day_closed["daySeconds"] == 9 * 3600
    assert repo.db.daily_author_activity.items[0]["breakSeconds"] == 15 * 60
    telegram_activity = next(item for item in repo.db.daily_author_activity.items if item.get("source") == "telegram")
    assert telegram_activity["daySeconds"] == 9 * 3600


def test_repeated_afk_does_not_reset_break_start():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane"})

    first = repo.record_break_event("dmitry_shane", "afk", "2026-04-28T12:00:00Z")
    second = repo.record_break_event("dmitry_shane", "afk", "2026-04-28T12:10:00Z")
    closed = repo.record_break_event("dmitry_shane", "online", "2026-04-28T12:20:00Z")

    assert first["status"] == "break_started"
    assert second["status"] == "break_already_started"
    assert closed["breakSeconds"] == 20 * 60


def test_break_close_handles_naive_mongo_datetime():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane"})
    repo.db.break_sessions.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "telegramUsername": "dmitry_shane",
            "startedAt": dt.datetime(2026, 4, 28, 12, 0),
        }
    )

    closed = repo.record_break_event("dmitry_shane", "online", "2026-04-28T12:20:00Z")

    assert closed["status"] == "break_closed"
    assert closed["breakSeconds"] == 20 * 60


def test_author_day_consumption_spans_plugin_sources():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {"source": "ual", "projectId": "unity", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 8 * 3600, "idleSeconds": 0}
    )
    repo.db.daily_author_activity.insert_one(
        {"source": "bal", "projectId": "blender", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 1800, "idleSeconds": 0}
    )

    consumed = repo._normal_microseconds_consumed_for_event(
        {"author": "Dmitry Shane", "date": "2026-04-28", "source": "bal", "projectId": "blender"}
    )

    assert consumed == ((8 * 3600) + 1800) * 1_000_000


def test_author_day_consumption_includes_figma_source():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {"source": "ual", "projectId": "unity", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 4 * 3600, "idleSeconds": 0}
    )
    repo.db.daily_author_activity.insert_one(
        {"source": "bal", "projectId": "blender", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 3 * 3600, "idleSeconds": 0}
    )
    repo.db.daily_author_activity.insert_one(
        {"source": "fch", "projectId": "figma", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 2 * 3600, "idleSeconds": 0}
    )

    consumed = repo._normal_microseconds_consumed_for_event(
        {"author": "Dmitry Shane", "date": "2026-04-28", "source": "fch", "projectId": "figma"}
    )

    assert consumed == 9 * 3600 * 1_000_000


def test_raw_event_state_spans_unity_and_blender_sources():
    repo = fake_repository()
    unity_event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    blender_event = {
        "source": "bal",
        "author": "Dmitry Shane",
        "projectId": "blender",
        "sessionId": "blender-session",
        "date": "2026-04-28",
        "eventType": "scene_changed",
        "occurredAtUtc": "2026-04-28T10:00:30Z",
        "occurredAtLocal": "2026-04-28T10:00:30+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, 30, tzinfo=dt.UTC),
        "metadata": {"inputType": "MOUSEMOVE"},
    }
    unity_heartbeat = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:20Z",
        "occurredAtLocal": "2026-04-28T10:01:20+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, 20, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(unity_event)
    blender_deltas = repo._apply_raw_event_to_aggregates(blender_event)
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(unity_heartbeat)

    assert blender_deltas["activeDeltaSeconds"] == 30
    assert heartbeat_deltas["idleDeltaSeconds"] == 0
    assert heartbeat_deltas["activeDeltaSeconds"] == 0


def test_raw_event_state_spans_unity_blender_and_figma_sources():
    repo = fake_repository()
    unity_event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    blender_event = {
        **unity_event,
        "source": "bal",
        "projectId": "blender",
        "sessionId": "blender-session",
        "eventType": "scene_changed",
        "occurredAtUtc": "2026-04-28T10:00:30Z",
        "occurredAtLocal": "2026-04-28T10:00:30+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, 30, tzinfo=dt.UTC),
        "metadata": {"inputType": "MOUSEMOVE"},
    }
    figma_event = {
        **unity_event,
        "source": "fch",
        "projectId": "figma",
        "sessionId": "figma-session",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:01:00Z",
        "occurredAtLocal": "2026-04-28T10:01:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, tzinfo=dt.UTC),
    }
    figma_heartbeat = {
        **figma_event,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:02:20Z",
        "occurredAtLocal": "2026-04-28T10:02:20+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 2, 20, tzinfo=dt.UTC),
    }
    unity_heartbeat = {
        **unity_event,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:02:24Z",
        "occurredAtLocal": "2026-04-28T10:02:24+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 2, 24, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(unity_event)
    blender_deltas = repo._apply_raw_event_to_aggregates(blender_event)
    figma_deltas = repo._apply_raw_event_to_aggregates(figma_event)
    figma_heartbeat_deltas = repo._apply_raw_event_to_aggregates(figma_heartbeat)
    unity_heartbeat_deltas = repo._apply_raw_event_to_aggregates(unity_heartbeat)

    assert blender_deltas["activeDeltaSeconds"] == 30
    assert figma_deltas["activeDeltaSeconds"] == 30
    assert figma_heartbeat_deltas["idleDeltaSeconds"] == 80
    assert unity_heartbeat_deltas["idleDeltaSeconds"] == 0
    assert unity_heartbeat_deltas["activeDeltaSeconds"] == 0


def test_second_plugin_heartbeat_does_not_create_tiny_duplicate_idle_row():
    repo = fake_repository()
    activity = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    unity_heartbeat = {
        **activity,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:06:00Z",
        "occurredAtLocal": "2026-04-28T10:06:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 6, tzinfo=dt.UTC),
    }
    blender_heartbeat = {
        **activity,
        "source": "bal",
        "projectId": "blender",
        "sessionId": "blender-session",
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:06:04Z",
        "occurredAtLocal": "2026-04-28T10:06:04+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 6, 4, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(activity)
    unity_deltas = repo._apply_raw_event_to_aggregates(unity_heartbeat)
    blender_deltas = repo._apply_raw_event_to_aggregates(blender_heartbeat)

    assert unity_deltas["idleDeltaSeconds"] == 6 * 60
    assert blender_deltas["idleDeltaSeconds"] == 0
    assert blender_deltas["activeDeltaSeconds"] == 0


def test_idle_is_accounted_by_last_activity_source_not_unrelated_heartbeat():
    repo = fake_repository()
    unity_event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    blender_event = {
        **unity_event,
        "source": "bal",
        "projectId": "blender",
        "sessionId": "blender-session",
        "eventType": "scene_changed",
        "occurredAtUtc": "2026-04-28T10:00:20Z",
        "occurredAtLocal": "2026-04-28T10:00:20+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, 20, tzinfo=dt.UTC),
        "metadata": {"inputType": "MOUSEMOVE"},
    }
    unity_heartbeat = {
        **unity_event,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:20Z",
        "occurredAtLocal": "2026-04-28T10:01:20+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, 20, tzinfo=dt.UTC),
    }
    blender_heartbeat = {
        **blender_event,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:25Z",
        "occurredAtLocal": "2026-04-28T10:01:25+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, 25, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(unity_event)
    repo._apply_raw_event_to_aggregates(blender_event)
    unity_deltas = repo._apply_raw_event_to_aggregates(unity_heartbeat)
    blender_deltas = repo._apply_raw_event_to_aggregates(blender_heartbeat)

    assert unity_deltas["idleDeltaSeconds"] == 0
    assert unity_deltas["activeDeltaSeconds"] == 0
    assert blender_deltas["idleDeltaSeconds"] == 65
    assert blender_deltas["activeDeltaSeconds"] == 0


def test_blender_scene_changed_without_input_metadata_is_not_activity():
    repo = fake_repository()
    file_saved = {
        "source": "bal",
        "author": "Dmitry Shane",
        "projectId": "blender",
        "sessionId": "blender-session",
        "date": "2026-04-28",
        "eventType": "file_saved",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
        "metadata": {"path": "/project/Scene.blend", "name": "Scene.blend"},
    }
    background_scene_changed = {
        **file_saved,
        "eventType": "scene_changed",
        "occurredAtUtc": "2026-04-28T10:00:03Z",
        "occurredAtLocal": "2026-04-28T10:00:03+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, 3, tzinfo=dt.UTC),
        "metadata": {"filepath": "/project/Scene.blend"},
    }
    heartbeat = {
        **file_saved,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:03Z",
        "occurredAtLocal": "2026-04-28T10:01:03+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, 3, tzinfo=dt.UTC),
        "metadata": {},
    }

    repo._apply_raw_event_to_aggregates(file_saved)
    background_deltas = repo._apply_raw_event_to_aggregates(background_scene_changed)
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(heartbeat)

    assert background_deltas["activeDeltaSeconds"] == 0
    assert background_deltas["idleDeltaSeconds"] == 0
    assert background_deltas["activityCountDeltas"] == []
    assert heartbeat_deltas["idleDeltaSeconds"] == 63
    assert heartbeat_deltas["activeDeltaSeconds"] == 0


def test_manual_report_requested_is_not_author_activity():
    repo = fake_repository()
    event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "manual_report_requested",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
        "metadata": {"reason": "server_request"},
    }

    deltas = repo._apply_raw_event_to_aggregates(event)

    assert deltas["activeDeltaSeconds"] == 0
    assert deltas["idleDeltaSeconds"] == 0
    assert deltas["activityCountDeltas"] == []


def test_heartbeat_idle_threshold_uses_plugin_interval():
    repo = fake_repository()
    activity = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    heartbeat = {
        **activity,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:00Z",
        "occurredAtLocal": "2026-04-28T10:01:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(activity)
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(heartbeat)

    assert heartbeat_deltas["idleDeltaSeconds"] == 60
    assert heartbeat_deltas["activeDeltaSeconds"] == 0


def test_blender_file_saved_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "bal",
            "eventType": "file_saved",
            "metadata": {
                "path": "/projects/scene/Shot01.blend",
                "name": "Shot01.blend",
            },
        }
    )

    assert saved == {"path": "/projects/scene/Shot01.blend", "name": "Shot01.blend", "saveCount": 1}


def test_figma_file_saved_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "fch",
            "eventType": "file_saved",
            "metadata": {
                "path": "https://www.figma.com/design/abc123/Game-HUD",
                "name": "Game HUD",
                "fileKey": "abc123",
            },
        }
    )

    assert saved == {
        "path": "https://www.figma.com/design/abc123/Game-HUD",
        "name": "Game HUD",
        "saveCount": 1,
    }


def test_vscode_file_saved_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "vsc",
            "eventType": "file_saved",
            "projectId": "game",
            "metadata": {
                "path": "/projects/game/Assets/Scripts/PlayerController.cs",
                "name": "PlayerController.cs",
                "languageId": "csharp",
            },
        }
    )

    assert saved == {
        "path": "/projects/game/Assets/Scripts/PlayerController.cs",
        "name": "PlayerController.cs",
        "saveCount": 1,
    }


def test_cursor_file_saved_is_counted_as_saved_file_with_project():
    saved = _saved_prefab_delta(
        {
            "source": "cur",
            "eventType": "file_saved",
            "projectId": "AL",
            "metadata": {
                "path": "/projects/AL/apps/frontend/src/main.tsx",
                "name": "main.tsx",
                "languageId": "typescriptreact",
            },
        }
    )

    assert saved == {
        "path": "/projects/AL/apps/frontend/src/main.tsx",
        "name": "main.tsx",
        "projectId": "AL",
        "saveCount": 1,
    }


def test_author_alias_rebuilds_raw_events_into_target_author():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Unknown User", "displayName": "Unknown User"})
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "figma-1",
            "source": "fch",
            "pluginVersion": "0.1.0",
            "author": "Unknown User",
            "authorEmail": "",
            "projectId": "figma",
            "sessionId": "figma-session",
            "deviceId": "figma-device",
            "batchId": "batch-1",
            "rawReportId": "raw-1",
            "reportType": "auto",
            "date": "2026-04-29",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "metadata": {},
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "figma-2",
            "source": "fch",
            "pluginVersion": "0.1.0",
            "author": "Unknown User",
            "authorEmail": "",
            "projectId": "figma",
            "sessionId": "figma-session",
            "deviceId": "figma-device",
            "batchId": "batch-1",
            "rawReportId": "raw-1",
            "reportType": "auto",
            "date": "2026-04-29",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 10, 1, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T10:01:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 1, tzinfo=dt.UTC),
            "metadata": {},
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "figma-3",
            "source": "fch",
            "pluginVersion": "0.1.0",
            "author": "Unknown User",
            "authorEmail": "",
            "projectId": "figma",
            "sessionId": "figma-session",
            "deviceId": "figma-device",
            "batchId": "batch-1",
            "rawReportId": "raw-1",
            "reportType": "auto",
            "date": "2026-04-29",
            "eventType": "file_saved",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 10, 1, 1, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T10:01:01+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 1, 1, tzinfo=dt.UTC),
            "metadata": {
                "path": "https://www.figma.com/design/abc123/Game-HUD",
                "name": "Game HUD",
                "fileKey": "abc123",
            },
        }
    )

    result = repo.upsert_author_alias("Unknown User", "Dmitry Shane")
    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    authors = {author["rawAuthor"]: author for author in summary["authors"]}
    profiles = {profile["rawAuthor"]: profile for profile in summary["profiles"]}

    assert result["ok"] is True
    assert "Unknown User" not in authors
    assert "Unknown User" not in profiles
    assert authors["Dmitry Shane"]["activeSeconds"] > 0
    assert authors["Dmitry Shane"]["savedPrefabs"] == [
        {"path": "https://www.figma.com/design/abc123/Game-HUD", "name": "Game HUD", "saveCount": 1}
    ]


def test_author_alias_delete_restores_source_author_listing():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "figma-1",
            "source": "fch",
            "pluginVersion": "0.1.0",
            "author": "Unknown User",
            "projectId": "figma",
            "sessionId": "figma-session",
            "date": "2026-04-29",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "metadata": {},
        }
    )

    repo.upsert_author_alias("Unknown User", "Dmitry Shane")
    repo.delete_author_alias("Unknown User")

    assert "Unknown User" in {profile["rawAuthor"] for profile in repo.author_profiles()}


def test_non_figma_file_saved_still_requires_blend_file():
    saved = _saved_prefab_delta(
        {
            "source": "bal",
            "eventType": "file_saved",
            "metadata": {
                "path": "https://www.figma.com/design/abc123/Game-HUD",
                "name": "Game HUD",
                "fileKey": "abc123",
            },
        }
    )

    assert saved is None


def test_live_telegram_summary_adds_open_day_and_break():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "telegramUsername": "dmitry_shane",
            "date": "2026-04-28",
            "startedAt": dt.datetime(2026, 4, 28, 9, 0, tzinfo=dt.UTC),
            "daySeconds": 0,
        }
    )
    repo.db.break_sessions.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "telegramUsername": "dmitry_shane",
            "startedAt": dt.datetime(2026, 4, 28, 9, 30, tzinfo=dt.UTC),
        }
    )
    totals = {"daySeconds": 0, "telegramDaySeconds": 0, "breakSeconds": 0}
    authors = {}

    repo._apply_live_telegram_summary(
        authors,
        {},
        totals,
        repo._profiles_by_raw_author(),
        {},
        {},
        "2026-04-28",
        "2026-04-28",
        None,
        dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    )

    assert totals["telegramDaySeconds"] == 3600
    assert totals["breakSeconds"] == 1800
    assert authors["Dmitry Shane"]["telegramDaySeconds"] == 3600
    assert authors["Dmitry Shane"]["breakSeconds"] == 1800


def test_hourly_break_subtracts_idle_with_active_priority():
    source = _empty_hourly_activity()
    source[16]["activeSeconds"] = 10 * 60
    source[16]["idleSeconds"] = 50 * 60
    breaks = _empty_hourly_activity()
    breaks[16]["breakSeconds"] = 30 * 60

    hourly = _apply_breaks_to_hourly_activity(source, breaks)

    assert hourly[16]["activeSeconds"] == 10 * 60
    assert hourly[16]["breakSeconds"] == 30 * 60
    assert hourly[16]["idleSeconds"] == 20 * 60


def test_hourly_break_consumption_prevents_double_counting_across_sources():
    first_source = _empty_hourly_activity()
    first_source[16]["idleSeconds"] = 50 * 60
    second_source = _empty_hourly_activity()
    second_source[16]["idleSeconds"] = 50 * 60
    breaks = _empty_hourly_activity()
    breaks[16]["breakSeconds"] = 30 * 60
    consumed = _empty_hourly_activity()

    first_hourly = _apply_breaks_to_hourly_activity(first_source, breaks, consumed)
    second_hourly = _apply_breaks_to_hourly_activity(second_source, breaks, consumed)

    assert first_hourly[16]["breakSeconds"] == 30 * 60
    assert second_hourly[16]["breakSeconds"] == 0
    assert consumed[16]["breakSeconds"] == 30 * 60


def test_totals_should_use_report_aggregates_not_hourly_buckets():
    source = _empty_hourly_activity()
    source[16]["activeSeconds"] = 60
    source[16]["idleSeconds"] = 60
    breaks = _empty_hourly_activity()
    hourly = _apply_breaks_to_hourly_activity(source, breaks)

    report_active_seconds = 20 * 60
    report_idle_seconds = 60 * 60
    break_seconds = sum(int(hour.get("breakSeconds", 0)) for hour in hourly)
    effective_active_seconds = report_active_seconds
    effective_idle_seconds = max(0, report_idle_seconds - break_seconds)

    assert effective_active_seconds == report_active_seconds
    assert effective_idle_seconds == report_idle_seconds


def test_plugin_day_time_is_capped_to_work_window():
    item = {"activeSeconds": 8 * 3600, "idleSeconds": 2 * 3600, "workWindowSeconds": 9 * 3600}

    assert _plugin_day_seconds(item) == 9 * 3600


def test_plugin_day_time_uses_snapshot_work_window():
    item = {"activeSeconds": 2 * 3600, "idleSeconds": 2 * 3600, "workWindowSeconds": 3 * 3600}

    assert _plugin_day_seconds(item) == 3 * 3600


def test_effective_idle_fits_inside_plugin_day_cap():
    plugin_day_seconds = 9 * 3600
    report_active_seconds = 30 * 60
    report_idle_seconds = 9 * 3600
    effective_active_seconds = min(report_active_seconds, plugin_day_seconds)
    effective_idle_seconds = min(report_idle_seconds, max(0, plugin_day_seconds - effective_active_seconds))

    assert effective_active_seconds == 30 * 60
    assert effective_idle_seconds == (8 * 3600) + (30 * 60)


def test_hourly_break_splits_across_hours():
    buckets = {("Dmitry Shane", "2026-04-28"): _empty_hourly_activity()}

    _add_break_interval_to_buckets(
        buckets,
        "Dmitry Shane",
        dt.datetime(2026, 4, 28, 16, 50, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 28, 17, 20, tzinfo=dt.UTC),
        "UTC",
    )

    assert buckets[("Dmitry Shane", "2026-04-28")][16]["breakSeconds"] == 10 * 60
    assert buckets[("Dmitry Shane", "2026-04-28")][17]["breakSeconds"] == 20 * 60


def test_hourly_break_splits_across_midnight():
    buckets = {
        ("Dmitry Shane", "2026-04-28"): _empty_hourly_activity(),
        ("Dmitry Shane", "2026-04-29"): _empty_hourly_activity(),
    }

    _add_break_interval_to_buckets(
        buckets,
        "Dmitry Shane",
        dt.datetime(2026, 4, 28, 23, 50, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 29, 0, 20, tzinfo=dt.UTC),
        "UTC",
    )

    assert buckets[("Dmitry Shane", "2026-04-28")][23]["breakSeconds"] == 10 * 60
    assert buckets[("Dmitry Shane", "2026-04-29")][0]["breakSeconds"] == 20 * 60


def test_hourly_break_uses_author_time_zone():
    buckets = {("Dmitry", "2026-04-29"): _empty_hourly_activity()}

    _add_break_interval_to_buckets(
        buckets,
        "Dmitry",
        dt.datetime(2026, 4, 29, 9, 36, 39, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 29, 10, 31, 59, tzinfo=dt.UTC),
        "Europe/Madrid",
    )

    assert buckets[("Dmitry", "2026-04-29")][9]["breakSeconds"] == 0
    assert buckets[("Dmitry", "2026-04-29")][10]["breakSeconds"] == 0
    assert buckets[("Dmitry", "2026-04-29")][11]["breakSeconds"] == 1401
    assert buckets[("Dmitry", "2026-04-29")][12]["breakSeconds"] == 1919


def test_hourly_break_suppresses_small_idle_artifact():
    source = _empty_hourly_activity()
    break_buckets = _empty_hourly_activity()
    source[15]["activeSeconds"] = 553
    source[15]["idleSeconds"] = 2991
    break_buckets[15]["breakSeconds"] = 2806

    hourly = _apply_breaks_to_hourly_activity(source, break_buckets)

    assert hourly[15]["activeSeconds"] == 553
    assert hourly[15]["breakSeconds"] == 2991
    assert hourly[15]["idleSeconds"] == 0


def test_hourly_activity_does_not_infer_small_report_gap_as_idle():
    source = _empty_hourly_activity()
    source[14]["activeSeconds"] = 3379

    hourly = _apply_breaks_to_hourly_activity(source, _empty_hourly_activity())

    assert hourly[14]["activeSeconds"] == 3379
    assert hourly[14]["idleSeconds"] == 0


def test_hourly_activity_does_not_infer_past_hour_gap_as_idle():
    source = _empty_hourly_activity()
    source[10]["activeSeconds"] = 1743
    source[10]["idleSeconds"] = 1143

    hourly = _apply_breaks_to_hourly_activity(source, _empty_hourly_activity())

    assert hourly[10]["activeSeconds"] == 1743
    assert hourly[10]["idleSeconds"] == 1143


def test_hourly_activity_keeps_dmitriy_zero_idle_gap_zero():
    source = _empty_hourly_activity()
    source[18]["activeSeconds"] = 3446

    hourly = _apply_breaks_to_hourly_activity(source, _empty_hourly_activity())

    assert hourly[18]["activeSeconds"] == 3446
    assert hourly[18]["idleSeconds"] == 0
    assert hourly[18]["breakSeconds"] == 0


def test_hourly_activity_preserves_fractional_active_segments():
    batch = _empty_event_deltas()
    local_start = dt.datetime(2026, 4, 29, 18, 0, 0, tzinfo=dt.UTC)
    segment_microseconds = [899_600_000, 900_400_000, 899_600_000, 900_400_000]
    cursor = local_start

    for microseconds in segment_microseconds:
        segment_end = cursor + dt.timedelta(microseconds=microseconds)
        deltas = _interval_deltas(cursor, segment_end, cursor, segment_end, True, 0)
        _merge_batch_deltas(batch, deltas)
        cursor = segment_end

    assert batch["activeDeltaSeconds"] == 3600
    assert batch["hourlyActivityDelta"][18]["activeSeconds"] == 3600
    assert batch["hourlyActivityDelta"][18]["idleSeconds"] == 0

    hourly = _apply_breaks_to_hourly_activity(batch["hourlyActivityDelta"], _empty_hourly_activity())

    assert hourly[18]["activeSeconds"] == 3600
    assert hourly[18]["idleSeconds"] == 0


def test_hourly_activity_does_not_infer_current_hour_idle():
    source = _empty_hourly_activity()
    source[18]["activeSeconds"] = 1800

    hourly = _apply_breaks_to_hourly_activity(source, _empty_hourly_activity())

    assert hourly[18]["activeSeconds"] == 1800
    assert hourly[18]["idleSeconds"] == 0
    assert hourly[19]["idleSeconds"] == 0
