from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import al_backend.repositories.settings as settings_repo
from al_backend.repositories.settings import SettingsRepository


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
    monkeypatch.setattr(settings_repo.shutil, "disk_usage", lambda _path: disk_usage(409600, 245760, 163840))
    monkeypatch.setattr(settings_repo, "_du_size_bytes", lambda _path: None)
    monkeypatch.setattr(
        settings_repo,
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
        settings_repo,
        "SERVER_STATS_SERVICES",
        (("backend", "AL Backend API", "al-backend.service"),),
    )
    monkeypatch.setattr(
        settings_repo,
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
    stats = repo.get_server_stats()

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


def test_server_stats_path_size_prefers_privileged_du(monkeypatch, tmp_path) -> None:
    path = tmp_path / "mongodb"
    path.mkdir()
    (path / "visible.bin").write_bytes(b"abc")

    monkeypatch.setattr(settings_repo, "_du_size_bytes", lambda _path: 370009988)

    assert settings_repo._path_size_bytes(path) == 370009988


def test_server_stats_service_falls_back_when_systemd_is_unavailable(monkeypatch) -> None:
    def raise_systemctl_error(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(settings_repo.subprocess, "run", raise_systemctl_error)

    service = settings_repo._server_stats_service("backend", "AL Backend API", "al-backend.service")

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

    monkeypatch.setattr(settings_repo.subprocess, "run", fake_systemctl)

    service = settings_repo._server_stats_service("backend", "AL Backend API", "al-backend.service")

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

    monkeypatch.setattr(settings_repo.subprocess, "run", fake_run)

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
