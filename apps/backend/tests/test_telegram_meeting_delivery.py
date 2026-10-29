import datetime as dt
import logging
import urllib.error

import pytest
from pymongo.errors import DuplicateKeyError

import al_backend.telegram_bot as bot
from tests.fakes import fake_repository


T0 = dt.datetime(2026, 10, 29, 15, 30, tzinfo=dt.UTC)


def meeting_repo(now=T0):
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "author_a"})
    repo.db.day_sessions.insert_one({
        "rawAuthor": "A", "date": now.date().isoformat(),
        "startedAt": now - dt.timedelta(hours=1), "lastOnlineAt": now - dt.timedelta(hours=1),
    })
    repo.upsert_meeting_notification_settings(
        enabled=True, author_raw_authors=["A"], time="16:30",
        time_zone_id="Europe/Madrid", days_of_week=list(range(7)),
    )
    return repo


def test_failed_send_retries_after_lease_then_stops_after_success(monkeypatch, caplog):
    repo = meeting_repo()
    now = T0
    monkeypatch.setattr(bot, "fetch_reminders_due_bundle", lambda *args: {
        "meetingNotifications": repo.claim_due_telegram_meeting_notifications(now),
    })
    monkeypatch.setattr(bot, "mark_reminder_sent", lambda url, secret, reminder_id, message_id, kind:
                        repo.mark_telegram_meeting_notification_sent(reminder_id, message_id))
    sends = []

    def send(*args):
        sends.append(args)
        if len(sends) == 1:
            raise urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer"))
        return {"ok": True, "result": {"message_id": 123}}

    monkeypatch.setattr(bot, "send_plain_message", send)
    config = bot.BotConfig(token="test-token", backend_url="http://backend", allowed_chat_id=1, bot_secret="test-secret")
    with pytest.raises(urllib.error.URLError):
        bot.send_due_reminders(config)
    doc = repo.db.telegram_meeting_notifications.items[0]
    assert doc["status"] == "claimed"
    assert doc["attemptCount"] == 1
    now = T0 + dt.timedelta(seconds=119)
    bot.send_due_reminders(config)
    assert len(sends) == 1
    now += dt.timedelta(seconds=1)
    with caplog.at_level(logging.INFO):
        bot.send_due_reminders(config)
    assert len(sends) == 2
    assert doc["status"] == "sent"
    assert doc["messageId"] == 123
    assert doc["attemptCount"] == 2
    assert "reason=claim_timeout" in caplog.text
    assert "test-token" not in caplog.text
    now += dt.timedelta(minutes=2)
    bot.send_due_reminders(config)
    assert len(sends) == 2


@pytest.mark.parametrize("already_claimed", [False, True])
@pytest.mark.parametrize("seconds", [900, 901, 3600])
def test_delivery_window_closes_at_boundary(already_claimed, seconds, caplog):
    repo = meeting_repo()
    if already_claimed:
        repo.claim_due_telegram_meeting_notifications(T0)
    with caplog.at_level(logging.INFO):
        assert repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(seconds=seconds)) == []
    doc = repo.db.telegram_meeting_notifications.items[0]
    assert doc["status"] == "closed"
    assert doc["closeAction"] == "delivery_window_expired"
    assert doc["attemptCount"] == int(already_claimed)
    assert "reason=delivery_window_expired" in caplog.text
    assert repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(hours=2)) == []


def test_delivery_allowed_just_before_deadline():
    repo = meeting_repo()
    assert len(repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(seconds=899))) == 1


@pytest.mark.parametrize("reason", ["meeting", "offline", "absence", "missing_username"])
def test_retry_rechecks_meeting_and_participants(reason):
    repo = meeting_repo()
    repo.claim_due_telegram_meeting_notifications(T0)
    if reason == "meeting":
        repo.db.meeting_recordings.insert_one({"recordingId": "meeting", "startedAt": T0 + dt.timedelta(seconds=30)})
    elif reason == "offline":
        repo.db.day_sessions.items[0]["lastOfflineAt"] = T0 + dt.timedelta(seconds=30)
    elif reason == "absence":
        repo.db.calendar_marks.insert_one({"rawAuthor": "A", "date": "2026-10-29"})
    else:
        repo.db.author_profiles.items[0].pop("telegramUsername")
    assert repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(seconds=120)) == []
    doc = repo.db.telegram_meeting_notifications.items[0]
    assert doc["closeAction"] == ("meeting_already_started" if reason == "meeting" else "empty_mention_list")
    assert doc["attemptCount"] == 1


@pytest.mark.parametrize("created_age,expected", [(119, False), (120, True), (None, True)])
def test_legacy_claim_without_last_claimed_at(created_age, expected):
    repo = meeting_repo()
    repo.claim_due_telegram_meeting_notifications(T0)
    doc = repo.db.telegram_meeting_notifications.items[0]
    doc.pop("lastClaimedAt")
    doc.pop("attemptCount")
    now = T0 + dt.timedelta(minutes=3)
    if created_age is None:
        doc.pop("createdAt")
    else:
        doc["createdAt"] = now - dt.timedelta(seconds=created_age)
    assert bool(repo.claim_due_telegram_meeting_notifications(now)) is expected
    if expected:
        assert doc["attemptCount"] == 1
        assert doc["lastClaimedAt"] == now


@pytest.mark.parametrize("keep_scheduled_at", [False, True])
def test_saved_schedule_controls_expiry_after_settings_change(keep_scheduled_at):
    repo = meeting_repo()
    repo.claim_due_telegram_meeting_notifications(T0)
    doc = repo.db.telegram_meeting_notifications.items[0]
    if not keep_scheduled_at:
        doc.pop("scheduledAt")
    repo.upsert_meeting_notification_settings(
        enabled=True, author_raw_authors=["A"], time="23:00", time_zone_id="Europe/Madrid", days_of_week=[0],
    )
    assert len(repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(minutes=2))) == 1
    if not keep_scheduled_at:
        doc.pop("scheduledAt")
    assert repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(minutes=15)) == []
    assert doc["closeAction"] == "delivery_window_expired"


@pytest.mark.parametrize("now", [T0, dt.datetime(2026, 7, 29, 14, 30, tzinfo=dt.UTC)])
def test_madrid_winter_and_summer_schedule(now):
    repo = meeting_repo(now)
    assert repo.claim_due_telegram_meeting_notifications(now - dt.timedelta(seconds=1)) == []
    assert len(repo.claim_due_telegram_meeting_notifications(now)) == 1
    assert repo.db.telegram_meeting_notifications.items[0]["scheduledAt"] == now


@pytest.mark.parametrize("retry", [False, True])
def test_interleaved_pollers_only_one_claims(monkeypatch, retry):
    repo = meeting_repo()
    if retry:
        repo.claim_due_telegram_meeting_notifications(T0)
    now = T0 + dt.timedelta(seconds=120) if retry else T0
    collection = repo.db.telegram_meeting_notifications
    original = collection.find_one_and_update
    winner = []

    def interleave(query, operation, **kwargs):
        monkeypatch.setattr(collection, "find_one_and_update", original)
        winner.extend(repo.claim_due_telegram_meeting_notifications(now))
        return original(query, operation, **kwargs)

    monkeypatch.setattr(collection, "find_one_and_update", interleave)
    assert repo.claim_due_telegram_meeting_notifications(now) == []
    assert len(winner) == 1
    assert collection.items[0]["attemptCount"] == (2 if retry else 1)


def test_duplicate_date_creation_race_reads_existing(monkeypatch):
    repo = meeting_repo()
    collection = repo.db.telegram_meeting_notifications
    original = collection.update_one

    def concurrent_insert(query, operation, **kwargs):
        operation["$setOnInsert"]["reminderId"] = "other-poller"
        original(query, operation, **kwargs)
        raise DuplicateKeyError("duplicate date")

    monkeypatch.setattr(collection, "update_one", concurrent_insert)
    due = repo.claim_due_telegram_meeting_notifications(T0)
    assert len(due) == 1
    assert due[0]["reminderId"] == "other-poller"
    assert collection.items[0]["attemptCount"] == 1


def test_expiry_does_not_overwrite_concurrent_sent(monkeypatch):
    repo = meeting_repo()
    due = repo.claim_due_telegram_meeting_notifications(T0)
    collection = repo.db.telegram_meeting_notifications
    original = collection.find_one_and_update

    def mark_sent_first(query, operation, **kwargs):
        repo.mark_telegram_meeting_notification_sent(due[0]["reminderId"], 123)
        return original(query, operation, **kwargs)

    monkeypatch.setattr(collection, "find_one_and_update", mark_sent_first)
    assert repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(minutes=15)) == []
    assert collection.items[0]["status"] == "sent"


def test_retry_refreshes_mentions_and_renews_lease():
    repo = meeting_repo()
    repo.db.author_profiles.insert_one({"rawAuthor": "B", "telegramUsername": "author_b"})
    repo.db.day_sessions.insert_one({
        "rawAuthor": "B", "date": "2026-10-29", "startedAt": T0, "lastOnlineAt": T0,
    })
    repo.upsert_meeting_notification_settings(
        enabled=True, author_raw_authors=["A", "B"], time="16:30",
        time_zone_id="Europe/Madrid", days_of_week=list(range(7)),
    )
    first = repo.claim_due_telegram_meeting_notifications(T0)[0]
    assert first["telegramUsernames"] == ["author_a", "author_b"]
    repo.db.day_sessions.items[0]["lastOfflineAt"] = T0 + dt.timedelta(seconds=30)
    retry = repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(seconds=120))[0]
    assert retry["reminderId"] == first["reminderId"]
    assert retry["telegramUsernames"] == ["author_b"]
    assert repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(seconds=239)) == []
    assert len(repo.claim_due_telegram_meeting_notifications(T0 + dt.timedelta(seconds=240))) == 1
    assert repo.db.telegram_meeting_notifications.items[0]["attemptCount"] == 3
