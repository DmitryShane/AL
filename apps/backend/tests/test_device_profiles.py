import datetime as dt

from tests.fakes import fake_repository


def test_device_profiles_list_includes_latest_identity_metadata():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device1", "targetRawAuthor": "Dmitry Shane"})
    repo.db.device_report_identities.insert_one(
        {
            "source": "dev",
            "deviceIdHash": "hash-1",
            "rawAuthor": "Device1",
            "createdAt": dt.datetime(2026, 5, 4, 9, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.raw_event_batches.insert_one(
        {
            "source": "dev",
            "author": "Device1",
            "deviceId": "keychain-device-id",
            "projectId": "Bike Rush 2",
            "pluginVersion": "0.1.0",
            "receivedAt": dt.datetime(2026, 5, 4, 10, 0, tzinfo=dt.UTC),
            "sentAt": "2026-05-04T12:58:00.0000000Z",
            "timeZoneId": "Europe/Kyiv",
            "timeZoneDisplayName": "EEST",
            "metadata": {
                "deviceAdvertisingId": "idfa-1",
                "platform": "IPhonePlayer",
                "trackingAuthorizationStatus": "denied",
            },
        }
    )

    profile = repo.device_profiles()[0]

    assert profile["rawDevice"] == "Device1"
    assert profile["linkedAuthor"] == "Dmitry Shane"
    assert profile["linkedAuthorDisplayName"] == "Dmitry Shane"
    assert profile["runtime"] == "iOS"
    assert profile["idfa"] == "idfa-1"
    assert profile["gaid"] == ""
    assert profile["trackingAuthorizationStatus"] == "denied"
    assert profile["timeZoneId"] == "Europe/Kyiv"
    assert profile["timeZoneDisplayName"] == "EEST"
    assert profile["deviceLastSeenAt"] == "2026-05-04T12:58:00.0000000Z"


def test_device_profiles_empty_database_returns_empty_list():
    repo = fake_repository()

    assert repo.device_profiles() == []


def test_device_profiles_use_natural_device_sort():
    repo = fake_repository()

    for raw_device in ["Device10", "Device2", "Device1"]:
        repo.db.device_report_identities.insert_one(
            {"source": "dev", "deviceIdHash": f"hash-{raw_device}", "rawAuthor": raw_device}
        )

    assert [profile["rawDevice"] for profile in repo.device_profiles()] == ["Device1", "Device2", "Device10"]


def test_device_profiles_set_gaid_for_android_only():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one(
        {"source": "dev", "deviceIdHash": "hash-android", "rawAuthor": "Device1"}
    )
    repo.db.raw_activity_events.insert_one(
        {
            "source": "dev",
            "author": "Device1",
            "receivedAt": dt.datetime(2026, 5, 4, 10, 0, tzinfo=dt.UTC),
            "metadata": {
                "deviceAdvertisingId": "gaid-1",
                "platform": "Android",
            },
        }
    )

    profile = repo.device_profiles()[0]

    assert profile["idfa"] == ""
    assert profile["gaid"] == "gaid-1"


def test_device_profiles_prefer_identity_latest_metadata():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one(
        {
            "source": "dev",
            "deviceIdHash": "hash-android",
            "rawAuthor": "Device1",
            "lastProjectId": "Bike Rush 2",
            "lastPluginVersion": "0.1.0",
            "lastSeenAt": dt.datetime(2026, 5, 4, 10, 0, tzinfo=dt.UTC),
            "firstSeenDeviceSentAt": "2026-05-04T09:55:00.0000000Z",
            "firstSeenTimeZoneId": "America/Vancouver",
            "firstSeenTimeZoneDisplayName": "PDT",
            "lastDeviceSentAt": "2026-05-04T10:55:00.0000000Z",
            "lastTimeZoneId": "America/Vancouver",
            "lastTimeZoneDisplayName": "PDT",
            "lastMetadata": {
                "deviceAdvertisingId": "gaid-1",
                "platform": "Android",
                "trackingAuthorizationStatus": "authorized",
            },
        }
    )

    profile = repo.device_profiles()[0]

    assert profile["projectId"] == "Bike Rush 2"
    assert profile["pluginVersion"] == "0.1.0"
    assert profile["gaid"] == "gaid-1"
    assert profile["trackingAuthorizationStatus"] == "authorized"
    assert profile["createdTimeZoneId"] == "America/Vancouver"
    assert profile["createdTimeZoneDisplayName"] == "PDT"
    assert profile["timeZoneId"] == "America/Vancouver"
    assert profile["timeZoneDisplayName"] == "PDT"
    assert profile["deviceCreatedAt"] == "2026-05-04T09:55:00.0000000Z"
    assert profile["deviceLastSeenAt"] == "2026-05-04T10:55:00.0000000Z"


def test_device_profiles_mark_editor_runtime():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one(
        {
            "source": "dev",
            "deviceIdHash": "hash-editor",
            "rawAuthor": "Device1",
            "lastMetadata": {
                "runtimePlatform": "OSXEditor",
            },
        }
    )

    profile = repo.device_profiles()[0]

    assert profile["runtime"] == "Editor"
    assert profile["idfa"] == ""
    assert profile["gaid"] == ""


def test_device_profile_alias_update_reuses_author_aliases():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.device_report_identities.insert_one(
        {"source": "dev", "deviceIdHash": "hash-1", "rawAuthor": "Device1"}
    )

    result = repo.upsert_device_profile_alias("Device1", "Dmitry Shane")

    assert result["ok"] is True
    assert repo.db.author_aliases.find_one({"sourceRawAuthor": "Device1"})["targetRawAuthor"] == "Dmitry Shane"


def test_device_profile_alias_rejects_missing_target_author():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one(
        {"source": "dev", "deviceIdHash": "hash-1", "rawAuthor": "Device1"}
    )

    result = repo.upsert_device_profile_alias("Device1", "Missing Author")

    assert result["ok"] is False


def test_device_author_profile_migration_removes_duplicate_author_profiles_only():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one(
        {"source": "dev", "deviceIdHash": "hash-1", "rawAuthor": "Device1"}
    )
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device1", "targetRawAuthor": "Dmitry Shane"})

    result = repo.migrate_device_author_profiles()

    assert result["deletedAuthorProfiles"] == 1
    assert repo.db.author_profiles.find_one({"rawAuthor": "Device1"}) is None
    assert repo.db.author_profiles.find_one({"rawAuthor": "Dmitry Shane"}) is not None
    assert repo.db.device_report_identities.find_one({"rawAuthor": "Device1"}) is not None
    assert repo.db.author_aliases.find_one({"sourceRawAuthor": "Device1"}) is not None


def test_delete_device_profile_removes_identity_alias_and_duplicate_profile_only():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one(
        {"source": "dev", "deviceIdHash": "hash-1", "rawAuthor": "Device1"}
    )
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device1", "targetRawAuthor": "Dmitry Shane"})
    repo.db.raw_activity_events.insert_one({"source": "dev", "author": "Device1", "eventId": "event-1"})

    result = repo.delete_device_profile("Device1")

    assert result["ok"] is True
    assert repo.db.device_report_identities.find_one({"rawAuthor": "Device1"}) is None
    assert repo.db.author_aliases.find_one({"sourceRawAuthor": "Device1"}) is None
    assert repo.db.author_profiles.find_one({"rawAuthor": "Device1"}) is None
    assert repo.db.author_profiles.find_one({"rawAuthor": "Dmitry Shane"}) is not None
    assert repo.db.raw_activity_events.find_one({"eventId": "event-1"}) is not None


def test_delete_all_device_profiles_removes_identities_aliases_and_duplicates_only():
    repo = fake_repository()
    for raw_device in ["Device1", "Device2"]:
        repo.db.device_report_identities.insert_one(
            {"source": "dev", "deviceIdHash": f"hash-{raw_device}", "rawAuthor": raw_device}
        )
        repo.db.author_profiles.insert_one({"rawAuthor": raw_device, "displayName": raw_device})
        repo.db.author_aliases.insert_one({"sourceRawAuthor": raw_device, "targetRawAuthor": "Dmitry Shane"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.raw_activity_events.insert_one({"source": "dev", "author": "Device1", "eventId": "event-1"})

    result = repo.delete_all_device_profiles()

    assert result["ok"] is True
    assert result["rawDeviceCount"] == 2
    assert result["rawDevices"] == ["Device1", "Device2"]
    assert repo.db.device_report_identities.count_documents({}) == 0
    assert repo.db.author_aliases.find_one({"sourceRawAuthor": "Device1"}) is None
    assert repo.db.author_aliases.find_one({"sourceRawAuthor": "Device2"}) is None
    assert repo.db.author_profiles.find_one({"rawAuthor": "Device1"}) is None
    assert repo.db.author_profiles.find_one({"rawAuthor": "Device2"}) is None
    assert repo.db.author_profiles.find_one({"rawAuthor": "Dmitry Shane"}) is not None
    assert repo.db.raw_activity_events.find_one({"eventId": "event-1"}) is not None
