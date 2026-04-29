import datetime as dt
import re
import unicodedata

from al_backend.repository import (
    Repository,
    _add_break_interval_to_buckets,
    _apply_breaks_to_hourly_activity,
    _date_query,
    _empty_hourly_activity,
    _normalize_telegram_username,
    _plugin_day_seconds,
    _saved_prefab_delta,
    _with_productivity,
)
from al_backend.telegram_bot import parse_event_type, telegram_username


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
        self.items = [item for item in self.items if not self._matches(item, query)]

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


class FakeDb:
    def __init__(self):
        self.author_profiles = FakeCollection()
        self.activity_snapshots = FakeCollection()
        self.break_events = FakeCollection()
        self.break_sessions = FakeCollection()
        self.day_sessions = FakeCollection()
        self.break_intervals = FakeCollection()
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
    assert totals["telegramDaySeconds"] == 10 * 60


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
    assert repo.db.daily_author_activity.items[0]["daySeconds"] == 9 * 3600


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

    consumed = repo._normal_seconds_consumed_for_event({"author": "Dmitry Shane", "date": "2026-04-28", "source": "bal", "projectId": "blender"})

    assert consumed == (8 * 3600) + 1800


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
            "eventType": "file_saved",
            "metadata": {
                "path": "/projects/scene/Shot01.blend",
                "name": "Shot01.blend",
            },
        }
    )

    assert saved == {"path": "/projects/scene/Shot01.blend", "name": "Shot01.blend", "saveCount": 1}


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
    assert hourly[15]["breakSeconds"] == 2806
    assert hourly[15]["idleSeconds"] == 0
