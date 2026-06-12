from __future__ import annotations

import datetime as dt
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any

from ..activity_math import _coerce_datetime


SERVER_STATS_PATHS = {
    "system": Path("/usr"),
    "app": Path("/opt/al"),
    "mongo": Path("/var/lib/mongodb"),
    "aptCache": Path("/var/cache/apt"),
    "logs": Path("/var/log"),
    "tmp": Path("/tmp"),
    "journalLogs": Path("/var/log/journal"),
    "mongoLogs": Path("/var/log/mongodb"),
    "nginxLogs": Path("/var/log/nginx"),
    "appUvCache": Path("/opt/al/.cache"),
    "appNpmCache": Path("/opt/al/.npm"),
}
SERVER_STATS_ACCOUNTING_PATHS = {
    "system": Path("/usr"),
    "var": Path("/var"),
    "app": Path("/opt/al"),
    "tmp": Path("/tmp"),
}
SERVER_STATS_SERVICES = (
    ("backend", "AL Backend API", "al-backend.service"),
    ("reportWorker", "AL Report Worker", "al-report-worker.service"),
    ("telegram", "AL Telegram Bot", "al-telegram-bot.service"),
    ("discord", "AL Discord Bot", "al-discord-bot.service"),
    ("mongo", "MongoDB", "mongod.service"),
    ("nginx", "Nginx", "nginx.service"),
)
SERVER_STATS_DAILY_REFRESH_HOUR_UTC = 4
SERVER_STATS_SNAPSHOT_CACHE_KIND = "server_stats_snapshot_v1"


def _server_stats_category(key: str, path: Path) -> dict[str, Any]:
    labels = {
        "system": "System /usr",
        "var": "Variable /var",
        "app": "App /opt/al",
        "mongo": "MongoDB",
        "aptCache": "apt cache",
        "logs": "Logs",
        "tmp": "Temporary files /tmp",
        "journalLogs": "systemd journal",
        "mongoLogs": "MongoDB logs",
        "nginxLogs": "Nginx logs",
        "appUvCache": "App uv cache",
        "appNpmCache": "App npm cache",
    }
    exists = path.exists()
    size = _path_size_bytes(path) if exists else 0
    return {
        "key": key,
        "label": labels.get(key, key),
        "path": str(path),
        "bytes": size,
        "exists": exists,
    }


def _path_size_bytes(path: Path) -> int:
    du_size = _du_size_bytes(path)
    if du_size is not None:
        return du_size

    if path.is_file():
        return path.stat().st_size

    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue

    return total


def _du_size_bytes(path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["sudo", "-n", "du", "-sb", "--", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    first_field = result.stdout.split(maxsplit=1)[0] if result.stdout.strip() else ""
    try:
        return int(first_field)
    except ValueError:
        return None


def _next_server_stats_refresh_at(now: dt.datetime | None = None) -> dt.datetime:
    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.UTC)
    current = current.astimezone(dt.UTC)
    candidate = current.replace(
        hour=SERVER_STATS_DAILY_REFRESH_HOUR_UTC,
        minute=0,
        second=0,
        microsecond=0,
    )
    if current >= candidate:
        candidate += dt.timedelta(days=1)
    return candidate


def _normalize_server_stats_refresh_mode(value: str | bool | None) -> str | None:
    if value is True:
        return "all"

    if value is False or value is None:
        return None

    normalized = str(value).strip().lower()

    if normalized in {"1", "true", "yes", "all"}:
        return "all"

    if normalized in {"disk", "services"}:
        return normalized

    return None


def _server_stats_service(key: str, label: str, unit: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "--property=ActiveState",
                "--property=SubState",
                "--property=LoadState",
                "--property=UnitFileState",
                "--property=ActiveEnterTimestamp",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return _unknown_server_stats_service(key, label, unit)

    values = _parse_systemctl_show(result.stdout)
    active_state = values.get("ActiveState") or "unknown"
    sub_state = values.get("SubState") or ""
    load_state = values.get("LoadState") or "unknown"
    unit_file_state = values.get("UnitFileState") or "unknown"
    active_entered_at = values.get("ActiveEnterTimestamp") or ""

    if active_state == "active":
        status = "running"
    elif active_state in {"inactive", "failed", "deactivating"}:
        status = "stopped"
    else:
        status = "unknown"

    return {
        "key": key,
        "label": label,
        "unit": unit,
        "status": status,
        "activeState": active_state or "unknown",
        "subState": sub_state or None,
        "loadState": load_state or "unknown",
        "unitFileState": unit_file_state or "unknown",
        "activeEnteredAt": active_entered_at or None,
    }


def _parse_systemctl_show(output: str) -> dict[str, str]:
    values: dict[str, str] = {}

    for line in output.splitlines():
        key, separator, value = line.partition("=")

        if separator:
            values[key] = value.strip()

    return values


def _unknown_server_stats_service(key: str, label: str, unit: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "status": "unknown",
        "activeState": "unknown",
        "subState": None,
        "loadState": "unknown",
        "unitFileState": "unknown",
        "activeEnteredAt": None,
    }


def _first_payload_event_project_id(payload: dict[str, Any]) -> str:
    events = payload.get("events")

    if not isinstance(events, list):
        return ""

    for event in events:
        if isinstance(event, dict) and str(event.get("projectId") or "").strip():
            return str(event.get("projectId") or "").strip()

    return ""


def _isoformat_datetime(value: Any) -> str | None:
    parsed = _coerce_datetime(value)
    return parsed.isoformat() if parsed else None


def _processing_seconds(report: dict[str, Any]) -> float | None:
    started_at = _coerce_datetime(report.get("processingStartedAt"))
    finished_at = _coerce_datetime(report.get("processedAt")) or _coerce_datetime(report.get("failedAt"))

    if not started_at or not finished_at:
        return None

    return max(0.0, round((finished_at - started_at).total_seconds(), 3))


def _reports_queue_row(report: dict[str, Any]) -> dict[str, Any]:
    payload = report.get("payload") if isinstance(report.get("payload"), dict) else {}
    project_id = str(payload.get("projectId") or "").strip() or _first_payload_event_project_id(payload)

    return {
        "id": str(report.get("_id") or ""),
        "receivedAt": _isoformat_datetime(report.get("receivedAt")),
        "queuedAt": _isoformat_datetime(report.get("queuedAt")),
        "processingStartedAt": _isoformat_datetime(report.get("processingStartedAt")),
        "processedAt": _isoformat_datetime(report.get("processedAt")),
        "failedAt": _isoformat_datetime(report.get("failedAt")),
        "source": str(report.get("source") or ""),
        "author": str(payload.get("author") or ""),
        "projectId": project_id,
        "status": str(report.get("status") or "unknown"),
        "attempts": int(report.get("attempts") or 0),
        "processingSeconds": _processing_seconds(report),
        "lastError": str(report.get("lastError") or ""),
    }


class ServerStatsServiceMixin:
    def get_server_stats(self, *, refresh: str | bool | None = None, background_tasks: Any | None = None) -> dict[str, Any]:
        self._ensure_server_stats_runtime_state()
        refresh_mode = _normalize_server_stats_refresh_mode(refresh)

        if refresh_mode == "services":
            self.refresh_server_stats_services()
        elif refresh_mode in {"all", "disk"} or getattr(self, "_server_stats_snapshot", None) is None:
            if getattr(self, "_server_stats_snapshot", None) is None:
                self._initialize_server_stats_fast_snapshot()
            self.start_server_stats_refresh()

        return self._server_stats_response()

    def start_server_stats_daily_refresh(self) -> None:
        self._ensure_server_stats_runtime_state()
        if self._server_stats_daily_thread is not None and self._server_stats_daily_thread.is_alive():
            return

        self._server_stats_stop_event.clear()
        self._server_stats_daily_thread = threading.Thread(
            target=self._server_stats_daily_refresh_loop,
            name="al-server-stats-daily-refresh",
            daemon=True,
        )
        self._server_stats_daily_thread.start()

        if self._server_stats_snapshot is None:
            self.start_server_stats_refresh()

    def stop_server_stats_daily_refresh(self) -> None:
        stop_event = getattr(self, "_server_stats_stop_event", None)
        if stop_event is not None:
            stop_event.set()

    def start_server_stats_refresh(self) -> bool:
        self._ensure_server_stats_runtime_state()
        with self._server_stats_lock:
            if self._server_stats_refreshing:
                return False
            now = dt.datetime.now(dt.UTC)
            self._server_stats_refreshing = True
            self._server_stats_refresh_started_at = now
            self._server_stats_last_refresh_error = None

        thread = threading.Thread(
            target=self._run_server_stats_refresh,
            name="al-server-stats-refresh",
            daemon=True,
        )
        thread.start()
        return True

    def refresh_server_stats_services(self) -> None:
        self._ensure_server_stats_runtime_state()
        services = [
            _server_stats_service(key, label, unit)
            for key, label, unit in SERVER_STATS_SERVICES
        ]
        now = dt.datetime.now(dt.UTC).isoformat()

        with self._server_stats_lock:
            if self._server_stats_snapshot is None:
                self._server_stats_snapshot = {
                    "generatedAt": now,
                    "hostname": socket.gethostname(),
                    "root": None,
                    "categories": [],
                    "services": services,
                    "cached": False,
                    "cacheExpiresAt": _next_server_stats_refresh_at().isoformat(),
                }
            else:
                self._server_stats_snapshot = {
                    **self._server_stats_snapshot,
                    "services": services,
                    "servicesGeneratedAt": now,
                }
            self._persist_server_stats_snapshot_locked()

    def _ensure_server_stats_runtime_state(self) -> None:
        if hasattr(self, "_server_stats_lock"):
            return
        self._server_stats_lock = threading.Lock()
        self._server_stats_snapshot = None
        self._server_stats_snapshot_at = None
        self._server_stats_refreshing = False
        self._server_stats_refresh_started_at = None
        self._server_stats_last_refresh_error = None
        self._server_stats_stop_event = threading.Event()
        self._server_stats_daily_thread = None
        persisted_snapshot = self._load_persisted_server_stats_snapshot()
        if persisted_snapshot:
            self._server_stats_snapshot, self._server_stats_snapshot_at = persisted_snapshot

    def _server_stats_daily_refresh_loop(self) -> None:
        while not self._server_stats_stop_event.is_set():
            next_refresh_at = _next_server_stats_refresh_at()
            wait_seconds = max(1.0, (next_refresh_at - dt.datetime.now(dt.UTC)).total_seconds())
            if self._server_stats_stop_event.wait(wait_seconds):
                return
            self.start_server_stats_refresh()

    def _run_server_stats_refresh(self) -> None:
        try:
            payload = self._build_server_stats_payload()
        except Exception as exc:
            with self._server_stats_lock:
                self._server_stats_last_refresh_error = str(exc)
                self._server_stats_refreshing = False
            return

        with self._server_stats_lock:
            self._server_stats_snapshot = dict(payload)
            self._server_stats_snapshot_at = dt.datetime.now(dt.UTC)
            self._server_stats_last_refresh_error = None
            self._server_stats_refreshing = False
            self._persist_server_stats_snapshot_locked()

    def _initialize_server_stats_fast_snapshot(self) -> None:
        payload = self._build_server_stats_fast_payload()
        with self._server_stats_lock:
            if self._server_stats_snapshot is not None:
                return

            self._server_stats_snapshot = payload
            self._server_stats_snapshot_at = dt.datetime.now(dt.UTC)
            self._persist_server_stats_snapshot_locked()

    def _load_persisted_server_stats_snapshot(self) -> tuple[dict[str, Any], dt.datetime | None] | None:
        if not hasattr(self, "db"):
            return None

        doc = self.db.system_settings.find_one({"kind": SERVER_STATS_SNAPSHOT_CACHE_KIND}, {"_id": 0}) or {}
        payload = doc.get("payload")

        if not isinstance(payload, dict) or not payload.get("root"):
            return None

        snapshot_at = _coerce_datetime(doc.get("snapshotAt")) or _coerce_datetime(payload.get("generatedAt"))
        return dict(payload), snapshot_at

    def _persist_server_stats_snapshot_locked(self) -> None:
        if not hasattr(self, "db") or not self._server_stats_snapshot:
            return

        snapshot_at = self._server_stats_snapshot_at or dt.datetime.now(dt.UTC)
        self.db.system_settings.update_one(
            {"kind": SERVER_STATS_SNAPSHOT_CACHE_KIND},
            {
                "$set": {
                    "kind": SERVER_STATS_SNAPSHOT_CACHE_KIND,
                    "payload": dict(self._server_stats_snapshot),
                    "snapshotAt": snapshot_at,
                    "updatedAt": dt.datetime.now(dt.UTC),
                }
            },
            upsert=True,
        )

    def _server_stats_response(self) -> dict[str, Any]:
        self._ensure_server_stats_runtime_state()
        with self._server_stats_lock:
            snapshot = dict(self._server_stats_snapshot) if self._server_stats_snapshot else None
            snapshot_at = self._server_stats_snapshot_at
            refreshing = self._server_stats_refreshing
            refresh_started_at = self._server_stats_refresh_started_at
            last_refresh_error = self._server_stats_last_refresh_error

        next_refresh_at = _next_server_stats_refresh_at()
        if snapshot is None:
            return {
                "ready": False,
                "cached": False,
                "refreshing": refreshing,
                "refreshStartedAt": refresh_started_at.isoformat() if refresh_started_at else None,
                "lastRefreshError": last_refresh_error,
                "nextScheduledRefreshAt": next_refresh_at.isoformat(),
                "generatedAt": None,
                "hostname": socket.gethostname(),
                "root": None,
                "categories": [],
                "services": [],
            }

        snapshot["ready"] = True
        snapshot["cached"] = True
        snapshot["refreshing"] = refreshing
        snapshot["refreshStartedAt"] = refresh_started_at.isoformat() if refresh_started_at else None
        snapshot["lastRefreshError"] = last_refresh_error
        snapshot["nextScheduledRefreshAt"] = next_refresh_at.isoformat()
        snapshot["cacheExpiresAt"] = next_refresh_at.isoformat()
        if snapshot_at is not None:
            snapshot["snapshotAt"] = snapshot_at.isoformat()
        return snapshot

    def _build_server_stats_payload(self) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        usage = shutil.disk_usage("/")
        total = int(usage.total)
        used = int(usage.used)
        free = int(usage.free)
        percent = round((used / total) * 100, 1) if total else 0.0
        categories = [
            _server_stats_category(key, path)
            for key, path in SERVER_STATS_PATHS.items()
        ]
        known_bytes = sum(
            _path_size_bytes(path)
            for path in SERVER_STATS_ACCOUNTING_PATHS.values()
            if path.exists()
        )
        other_bytes = max(0, used - known_bytes)

        categories.append(
            {
                "key": "other",
                "label": "Other",
                "path": "/",
                "bytes": other_bytes,
                "exists": True,
            }
        )

        if percent >= 90:
            warning_level = "critical"
        elif percent >= 80:
            warning_level = "warning"
        else:
            warning_level = "ok"

        payload = {
            "generatedAt": dt.datetime.now(dt.UTC).isoformat(),
            "hostname": socket.gethostname(),
            "root": {
                "path": "/",
                "totalBytes": total,
                "usedBytes": used,
                "freeBytes": free,
                "usedPercent": percent,
                "warningLevel": warning_level,
            },
            "categories": categories,
            "services": [
                _server_stats_service(key, label, unit)
                for key, label, unit in SERVER_STATS_SERVICES
            ],
            "cached": False,
            "cacheExpiresAt": _next_server_stats_refresh_at(now).isoformat(),
        }
        return payload

    def _build_server_stats_fast_payload(self) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        usage = shutil.disk_usage("/")
        total = int(usage.total)
        used = int(usage.used)
        free = int(usage.free)
        percent = round((used / total) * 100, 1) if total else 0.0

        if percent >= 90:
            warning_level = "critical"
        elif percent >= 80:
            warning_level = "warning"
        else:
            warning_level = "ok"

        return {
            "generatedAt": now.isoformat(),
            "hostname": socket.gethostname(),
            "root": {
                "path": "/",
                "totalBytes": total,
                "usedBytes": used,
                "freeBytes": free,
                "usedPercent": percent,
                "warningLevel": warning_level,
            },
            "categories": [],
            "services": [
                _server_stats_service(key, label, unit)
                for key, label, unit in SERVER_STATS_SERVICES
            ],
            "cached": False,
            "cacheExpiresAt": _next_server_stats_refresh_at(now).isoformat(),
        }

    def get_reports_queue_status(self) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        processed_since = now - dt.timedelta(hours=1)
        worker = _server_stats_service("reportWorker", "AL Report Worker", "al-report-worker.service")
        latest_processed = self.db.raw_reports.find_one(
            {"status": "processed", "processedAt": {"$exists": True}},
            sort=[("processedAt", -1)],
        )
        oldest_queued = self.db.raw_reports.find_one(
            {"status": "queued"},
            sort=[("queuedAt", 1), ("receivedAt", 1)],
        )
        recent_reports = list(
            self.db.raw_reports.find(
                {},
                {
                    "payload.author": 1,
                    "payload.projectId": 1,
                    "payload.events.projectId": 1,
                    "source": 1,
                    "receivedAt": 1,
                    "queuedAt": 1,
                    "processingStartedAt": 1,
                    "processedAt": 1,
                    "failedAt": 1,
                    "status": 1,
                    "attempts": 1,
                    "lastError": 1,
                },
            ).sort([("receivedAt", -1)]).limit(50)
        )
        failed_reports = list(
            self.db.raw_reports.find(
                {"status": "failed"},
                {
                    "payload.author": 1,
                    "payload.projectId": 1,
                    "payload.events.projectId": 1,
                    "source": 1,
                    "receivedAt": 1,
                    "queuedAt": 1,
                    "processingStartedAt": 1,
                    "processedAt": 1,
                    "failedAt": 1,
                    "status": 1,
                    "attempts": 1,
                    "lastError": 1,
                },
            ).sort([("failedAt", -1), ("receivedAt", -1)]).limit(20)
        )

        queued_at = _coerce_datetime(oldest_queued.get("queuedAt")) if oldest_queued else None
        oldest_queued_payload = None

        if oldest_queued and queued_at:
            oldest_queued_payload = {
                "receivedAt": _isoformat_datetime(oldest_queued.get("receivedAt")),
                "queuedAt": queued_at.isoformat(),
                "ageSeconds": max(0, int((now - queued_at).total_seconds())),
            }

        return {
            "generatedAt": now.isoformat(),
            "worker": {
                **worker,
                "lastProcessedAt": _isoformat_datetime(latest_processed.get("processedAt")) if latest_processed else None,
            },
            "counts": {
                "queued": self.db.raw_reports.count_documents({"status": "queued"}),
                "processing": self.db.raw_reports.count_documents({"status": "processing"}),
                "failed": self.db.raw_reports.count_documents({"status": "failed"}),
                "processedLastHour": self.db.raw_reports.count_documents({"status": "processed", "processedAt": {"$gte": processed_since}}),
            },
            "oldestQueued": oldest_queued_payload,
            "recentReports": [_reports_queue_row(report) for report in recent_reports],
            "failedReports": [_reports_queue_row(report) for report in failed_reports],
        }

    def reboot_server(self) -> dict[str, Any]:
        requested_at = dt.datetime.now(dt.UTC)
        services = ["mongod", "nginx", "al-backend", "al-report-worker", "al-telegram-bot", "al-discord-bot"]
        unit_name = f"al-dashboard-reboot-{requested_at.strftime('%Y%m%d%H%M%S')}"

        try:
            subprocess.run(
                [
                    "/usr/bin/sudo",
                    "-n",
                    "/usr/bin/systemd-run",
                    "--unit",
                    unit_name,
                    "--on-active=2s",
                    "/usr/bin/systemctl",
                    "restart",
                    *services,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": str(exc), "requestedAt": requested_at.isoformat()}

        return {"ok": True, "status": "services_restart_scheduled", "requestedAt": requested_at.isoformat(), "services": services}
