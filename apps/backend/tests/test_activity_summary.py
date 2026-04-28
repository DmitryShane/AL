import datetime as dt

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

    def _matches(self, item, query):
        for key, value in query.items():
            item_value = item.get(key)

            if isinstance(value, dict):
                if "$in" in value and item_value not in value["$in"]:
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
        self.break_events = FakeCollection()
        self.break_sessions = FakeCollection()
        self.day_sessions = FakeCollection()
        self.break_intervals = FakeCollection()
        self.daily_author_activity = FakeCollection()
        self.aggregate_session_state = FakeCollection()
        self.interval_settings = FakeCollection()
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
        "occurredAtUtc": "2026-04-28T10:03:00Z",
        "occurredAtLocal": "2026-04-28T10:03:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 3, tzinfo=dt.UTC),
    }
    unity_heartbeat = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:06:00Z",
        "occurredAtLocal": "2026-04-28T10:06:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 6, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(unity_event)
    blender_deltas = repo._apply_raw_event_to_aggregates(blender_event)
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(unity_heartbeat)

    assert blender_deltas["activeDeltaSeconds"] == 3 * 60
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
    )

    assert buckets[("Dmitry Shane", "2026-04-28")][23]["breakSeconds"] == 10 * 60
    assert buckets[("Dmitry Shane", "2026-04-29")][0]["breakSeconds"] == 20 * 60
