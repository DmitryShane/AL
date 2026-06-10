from __future__ import annotations

from collections import namedtuple
from pathlib import Path
import datetime as dt
import time
from types import SimpleNamespace

import al_backend.services.server_stats as server_stats_service
from al_backend.repositories.settings import SettingsRepository
from tests.fakes import fake_repository


def test_server_stats_shape_uses_read_only_disk_data(monkeypatch, tmp_path) -> None:
    root = tmp_path / "root"
    usr = root / "usr"
    opt = root / "opt" / "al"
    apt = root / "var" / "cache" / "apt"
    logs = root / "var" / "log"
    mongo = root / "var" / "lib" / "mongodb"

    for path in (usr, opt, apt, logs, mongo):
        path.mkdir(parents=True)
        (path / "sample.bin").write_bytes(b"abc")

    disk_usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(server_stats_service.shutil, "disk_usage", lambda _path: disk_usage(409600, 245760, 163840))
    monkeypatch.setattr(server_stats_service, "_du_size_bytes", lambda _path: None)
    monkeypatch.setattr(
        server_stats_service,
        "SERVER_STATS_PATHS",
        {
            "system": usr,
            "app": opt,
            "mongo": mongo,
            "aptCache": apt,
            "logs": logs,
            "missing": Path("/path/that/does/not/exist"),
        },
    )
    monkeypatch.setattr(
        server_stats_service,
        "SERVER_STATS_ACCOUNTING_PATHS",
        {
            "system": usr,
            "app": opt,
            "tmp": root / "tmp",
            "var": root / "var",
        },
    )
    monkeypatch.setattr(
        server_stats_service,
        "SERVER_STATS_SERVICES",
        (("backend", "AL Backend API", "al-backend.service"),),
    )
    monkeypatch.setattr(
        server_stats_service,
        "_server_stats_service",
        lambda key, label, unit: {
            "key": key,
            "label": label,
            "unit": unit,
            "status": "running",
            "activeState": "active",
            "subState": "running",
            "loadState": "loaded",
            "unitFileState": "enabled",
            "activeEnteredAt": "Tue 2026-05-05 19:00:00 UTC",
        },
    )

    repo = SettingsRepository.__new__(SettingsRepository)
    stats = repo._build_server_stats_payload()

    assert stats["root"]["totalBytes"] == 409600
    assert stats["root"]["usedBytes"] == 245760
    assert stats["root"]["freeBytes"] == 163840
    assert stats["root"]["warningLevel"] == "ok"
    assert {item["key"] for item in stats["categories"]} >= {"system", "app", "mongo", "aptCache", "logs", "other"}
    assert next(item for item in stats["categories"] if item["key"] == "missing")["exists"] is False
    assert stats["services"] == [
        {
            "key": "backend",
            "label": "AL Backend API",
            "unit": "al-backend.service",
            "status": "running",
            "activeState": "active",
            "subState": "running",
            "loadState": "loaded",
            "unitFileState": "enabled",
            "activeEnteredAt": "Tue 2026-05-05 19:00:00 UTC",
        }
    ]
    assert stats["cached"] is False


def test_server_stats_returns_existing_snapshot_without_heavy_scan(monkeypatch) -> None:
    repo = SettingsRepository.__new__(SettingsRepository)
    repo._ensure_server_stats_runtime_state()
    repo._server_stats_snapshot = {
        "generatedAt": "2026-06-09T04:00:00+00:00",
        "hostname": "server",
        "root": {
            "path": "/",
            "totalBytes": 1000,
            "usedBytes": 500,
            "freeBytes": 500,
            "usedPercent": 50.0,
            "warningLevel": "ok",
        },
        "categories": [],
        "services": [],
    }
    repo._server_stats_snapshot_at = dt.datetime(2026, 6, 9, 4, 0, tzinfo=dt.UTC)

    def fail_disk_usage(_path):
        raise AssertionError("server stats request must not scan disk when snapshot exists")

    monkeypatch.setattr(server_stats_service.shutil, "disk_usage", fail_disk_usage)
    stats = repo.get_server_stats()

    assert stats["ready"] is True
    assert stats["cached"] is True
    assert stats["root"]["usedBytes"] == 500


def test_server_stats_refresh_returns_current_snapshot_immediately(monkeypatch) -> None:
    repo = SettingsRepository.__new__(SettingsRepository)
    repo._ensure_server_stats_runtime_state()
    repo._server_stats_snapshot = {
        "generatedAt": "2026-06-09T04:00:00+00:00",
        "hostname": "server",
        "root": {
            "path": "/",
            "totalBytes": 1000,
            "usedBytes": 500,
            "freeBytes": 500,
            "usedPercent": 50.0,
            "warningLevel": "ok",
        },
        "categories": [],
        "services": [],
    }

    calls = {"count": 0}

    def fake_start_refresh():
        calls["count"] += 1
        repo._server_stats_refreshing = True
        repo._server_stats_refresh_started_at = dt.datetime(2026, 6, 9, 4, 1, tzinfo=dt.UTC)
        return True

    monkeypatch.setattr(repo, "start_server_stats_refresh", fake_start_refresh)

    stats = repo.get_server_stats(refresh=True)

    assert stats["ready"] is True
    assert stats["refreshing"] is True
    assert stats["root"]["usedBytes"] == 500
    assert calls["count"] == 1


def test_server_stats_services_refresh_updates_services_without_disk_scan(monkeypatch) -> None:
    repo = SettingsRepository.__new__(SettingsRepository)
    repo._ensure_server_stats_runtime_state()
    repo._server_stats_snapshot = {
        "generatedAt": "2026-06-09T04:00:00+00:00",
        "hostname": "server",
        "root": {
            "path": "/",
            "totalBytes": 1000,
            "usedBytes": 500,
            "freeBytes": 500,
            "usedPercent": 50.0,
            "warningLevel": "ok",
        },
        "categories": [],
        "services": [
            {
                "key": "backend",
                "label": "AL Backend API",
                "unit": "al-backend.service",
                "status": "unknown",
                "activeState": "unknown",
                "loadState": "unknown",
                "unitFileState": "unknown",
            }
        ],
    }

    def fail_disk_usage(_path):
        raise AssertionError("services refresh must not scan disk")

    monkeypatch.setattr(server_stats_service.shutil, "disk_usage", fail_disk_usage)
    monkeypatch.setattr(
        server_stats_service,
        "SERVER_STATS_SERVICES",
        (("backend", "AL Backend API", "al-backend.service"),),
    )
    monkeypatch.setattr(
        server_stats_service,
        "_server_stats_service",
        lambda key, label, unit: {
            "key": key,
            "label": label,
            "unit": unit,
            "status": "running",
            "activeState": "active",
            "subState": "running",
            "loadState": "loaded",
            "unitFileState": "enabled",
            "activeEnteredAt": "Tue 2026-05-05 19:00:00 UTC",
        },
    )

    stats = repo.get_server_stats(refresh="services")

    assert stats["root"]["usedBytes"] == 500
    assert stats["services"][0]["status"] == "running"
    assert stats["servicesGeneratedAt"]


def test_server_stats_single_flight_refresh(monkeypatch) -> None:
    repo = SettingsRepository.__new__(SettingsRepository)
    repo._ensure_server_stats_runtime_state()

    def slow_build():
        time.sleep(0.05)
        return {
            "generatedAt": "2026-06-09T04:00:00+00:00",
            "hostname": "server",
            "root": {
                "path": "/",
                "totalBytes": 1000,
                "usedBytes": 500,
                "freeBytes": 500,
                "usedPercent": 50.0,
                "warningLevel": "ok",
            },
            "categories": [],
            "services": [],
        }

    monkeypatch.setattr(repo, "_build_server_stats_payload", slow_build)

    assert repo.start_server_stats_refresh() is True
    assert repo.start_server_stats_refresh() is False

    deadline = time.time() + 1
    while repo._server_stats_refreshing and time.time() < deadline:
        time.sleep(0.01)
    assert repo._server_stats_snapshot is not None


def test_server_stats_cold_start_returns_fast_snapshot_without_heavy_scan(monkeypatch) -> None:
    repo = SettingsRepository.__new__(SettingsRepository)
    disk_usage = namedtuple("usage", "total used free")

    def fake_start_refresh():
        repo._ensure_server_stats_runtime_state()
        repo._server_stats_refreshing = True
        repo._server_stats_refresh_started_at = dt.datetime(2026, 6, 9, 4, 1, tzinfo=dt.UTC)
        return True

    def fail_path_size(_path):
        raise AssertionError("cold server stats request must not scan category paths")

    monkeypatch.setattr(repo, "start_server_stats_refresh", fake_start_refresh)
    monkeypatch.setattr(server_stats_service.shutil, "disk_usage", lambda _path: disk_usage(1000, 250, 750))
    monkeypatch.setattr(server_stats_service, "_path_size_bytes", fail_path_size)
    monkeypatch.setattr(server_stats_service, "SERVER_STATS_SERVICES", ())

    stats = repo.get_server_stats()

    assert stats["ready"] is True
    assert stats["refreshing"] is True
    assert stats["root"]["usedBytes"] == 250
    assert stats["categories"] == []


def test_server_stats_loads_persisted_snapshot_after_restart(monkeypatch) -> None:
    repo = fake_repository()
    repo.db.system_settings.insert_one(
        {
            "kind": server_stats_service.SERVER_STATS_SNAPSHOT_CACHE_KIND,
            "payload": {
                "generatedAt": "2026-06-09T04:00:00+00:00",
                "hostname": "server",
                "root": {
                    "path": "/",
                    "totalBytes": 1000,
                    "usedBytes": 500,
                    "freeBytes": 500,
                    "usedPercent": 50.0,
                    "warningLevel": "ok",
                },
                "categories": [{"key": "app", "label": "App /opt/al", "path": "/opt/al", "bytes": 100, "exists": True}],
                "services": [],
            },
            "snapshotAt": dt.datetime(2026, 6, 9, 4, 0, tzinfo=dt.UTC),
        }
    )

    def fail_disk_usage(_path):
        raise AssertionError("server stats request must use persisted snapshot after restart")

    monkeypatch.setattr(server_stats_service.shutil, "disk_usage", fail_disk_usage)
    stats = repo.get_server_stats()

    assert stats["ready"] is True
    assert stats["root"]["usedBytes"] == 500
    assert stats["categories"][0]["key"] == "app"


def test_next_server_stats_refresh_at_uses_0400_utc() -> None:
    before = dt.datetime(2026, 6, 9, 3, 30, tzinfo=dt.UTC)
    after = dt.datetime(2026, 6, 9, 4, 30, tzinfo=dt.UTC)

    assert server_stats_service._next_server_stats_refresh_at(before) == dt.datetime(2026, 6, 9, 4, 0, tzinfo=dt.UTC)
    assert server_stats_service._next_server_stats_refresh_at(after) == dt.datetime(2026, 6, 10, 4, 0, tzinfo=dt.UTC)


def test_settings_bootstrap_is_lightweight(monkeypatch) -> None:
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "displayName": "Author A"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device A", "targetRawAuthor": "A"})

    def fail_activity_summary(*_args, **_kwargs):
        raise AssertionError("settings bootstrap must not build activity summary")

    monkeypatch.setattr(repo, "cached_activity_summary", fail_activity_summary)
    payload = repo.get_settings_bootstrap()

    assert payload["intervalSettings"]["defaultSendIntervalSeconds"] == 60
    assert payload["discordSettings"]["meetingAutoAfkTimeoutSeconds"] == 600
    assert payload["activitySummary"]["authors"] == []
    assert payload["activitySummary"]["profiles"][0]["rawAuthor"] == "A"
    assert payload["activitySummary"]["authorAliases"][0]["sourceRawAuthor"] == "Device A"


def test_server_stats_path_size_prefers_privileged_du(monkeypatch, tmp_path) -> None:
    path = tmp_path / "mongodb"
    path.mkdir()
    (path / "visible.bin").write_bytes(b"abc")

    monkeypatch.setattr(server_stats_service, "_du_size_bytes", lambda _path: 370009988)

    assert server_stats_service._path_size_bytes(path) == 370009988


def test_server_stats_service_falls_back_when_systemd_is_unavailable(monkeypatch) -> None:
    def raise_systemctl_error(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(server_stats_service.subprocess, "run", raise_systemctl_error)

    service = server_stats_service._server_stats_service("backend", "AL Backend API", "al-backend.service")

    assert service == {
        "key": "backend",
        "label": "AL Backend API",
        "unit": "al-backend.service",
        "status": "unknown",
        "activeState": "unknown",
        "subState": None,
        "loadState": "unknown",
        "unitFileState": "unknown",
        "activeEnteredAt": None,
    }


def test_server_stats_service_parses_named_systemd_fields(monkeypatch) -> None:
    def fake_systemctl(*_args, **_kwargs):
        return SimpleNamespace(
            stdout=(
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "SubState=running\n"
                "UnitFileState=enabled\n"
                "ActiveEnterTimestamp=Tue 2026-05-05 19:00:00 UTC\n"
            )
        )

    monkeypatch.setattr(server_stats_service.subprocess, "run", fake_systemctl)

    service = server_stats_service._server_stats_service("backend", "AL Backend API", "al-backend.service")

    assert service["status"] == "running"
    assert service["activeState"] == "active"
    assert service["subState"] == "running"
    assert service["loadState"] == "loaded"
    assert service["unitFileState"] == "enabled"
    assert service["activeEnteredAt"] == "Tue 2026-05-05 19:00:00 UTC"


def test_server_reboot_schedules_delayed_systemd_restart(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace()

    monkeypatch.setattr(server_stats_service.subprocess, "run", fake_run)

    repo = SettingsRepository.__new__(SettingsRepository)
    result = repo.reboot_server()

    assert result["ok"] is True
    assert result["status"] == "services_restart_scheduled"
    args, kwargs = calls[0]
    assert args[:7] == [
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/systemd-run",
        "--unit",
        args[4],
        "--on-active=2s",
        "/usr/bin/systemctl",
    ]
    assert args[4].startswith("al-dashboard-reboot-")
    assert args[7:] == ["restart", "mongod", "nginx", "al-backend", "al-telegram-bot", "al-discord-bot"]
    assert kwargs["check"] is True
