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
    _with_productivity,
)
from al_backend.telegram_bot import (
    BotConfig,
    edit_reminder_message,
    get_updates,
    handle_callback_query,
    parse_event_type,
    parse_reminder_callback,
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
    assert parse_reminder_callback("hello") is None


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


def test_telegram_bot_callback_edits_message_after_close(monkeypatch):
    calls = []
    config = BotConfig(
        token="token",
        backend_url="https://activity.mempic.com",
        allowed_chat_id=123,
        bot_secret="secret",
    )

    def fake_close_reminder(backend_url, bot_secret, reminder_id, action, actor_telegram_username=""):
        calls.append(("close", backend_url, bot_secret, reminder_id, action, actor_telegram_username))
        return {"ok": True}

    def fake_edit_reminder_message(token, chat_id, message_id, action, telegram_username=""):
        calls.append(("edit", token, chat_id, message_id, action, telegram_username))
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

    assert ("close", "https://activity.mempic.com", "secret", "reminder-1", "overtime", "dmitryshane") in calls
    assert ("edit", "token", 123, 42, "overtime", "dmitryshane") in calls
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


def test_discord_author_mappings_update_known_telegram_profiles_only():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Evgeniy Dotsenko", "displayName": "Evgeniy Dotsenko", "telegramUsername": "ama_deus"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "telegramUsername": "igormats"})

    result = apply_discord_author_mappings(repo)
    evgeniy = repo.db.author_profiles.find_one({"telegramUsername": "ama_deus"})
    igor = repo.db.author_profiles.find_one({"telegramUsername": "igormats"})

    assert evgeniy["discordUserId"] == "645196366494171139"
    assert evgeniy["discordUsername"] == "Evgeniy Dotsenko"
    assert "discordUserId" not in igor
    assert [item["telegramUsername"] for item in result["updated"]] == ["ama_deus"]
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

    assert saved == {"path": "https://www.figma.com/design/abc123/Game-HUD", "name": "Game HUD", "saveCount": 1}


def test_vscode_file_saved_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "vsc",
            "eventType": "file_saved",
            "metadata": {
                "path": "/projects/game/Assets/Scripts/PlayerController.cs",
                "name": "PlayerController.cs",
                "languageId": "csharp",
            },
        }
    )

    assert saved == {"path": "/projects/game/Assets/Scripts/PlayerController.cs", "name": "PlayerController.cs", "saveCount": 1}


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
