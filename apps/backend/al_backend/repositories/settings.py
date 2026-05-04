from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

from ..activity_math import *
from ..author_avatar_cache import DEFAULT_AVATAR_REFRESH_CADENCE, normalize_avatar_refresh_cadence
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


SERVER_STATS_PATHS = {
    "system": Path("/usr"),
    "var": Path("/var"),
    "app": Path("/opt/al"),
    "mongo": Path("/var/lib/mongodb"),
    "aptCache": Path("/var/cache/apt"),
    "logs": Path("/var/log"),
}


def _server_stats_category(key: str, path: Path) -> dict[str, Any]:
    labels = {
        "system": "System /usr",
        "var": "Variable /var",
        "app": "App /opt/al",
        "mongo": "MongoDB",
        "aptCache": "apt cache",
        "logs": "Logs",
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


class SettingsRepository(MongoComposableMixin):
    def get_interval_for_author(self, author: str) -> int:
        author = _normalize_author(author)
        author_setting = self.db.interval_settings.find_one({"kind": "author", "author": author})

        if author_setting and author_setting.get("sendIntervalSeconds"):
            return int(author_setting["sendIntervalSeconds"])

        global_setting = self.db.interval_settings.find_one({"kind": "global"})

        if global_setting and global_setting.get("sendIntervalSeconds"):
            return int(global_setting["sendIntervalSeconds"])

        return composed(self).default_send_interval_seconds

    def get_idle_threshold_for_author(self, author: str) -> int:
        global_setting = self.db.interval_settings.find_one({"kind": "global"}) or {}

        if global_setting.get("idleThresholdSeconds"):
            return int(global_setting["idleThresholdSeconds"])

        return DEFAULT_IDLE_THRESHOLD_SECONDS

    def get_plugin_ingest_enabled(self) -> bool:
        settings = self.db.system_settings.find_one({"kind": "plugins"}, {"_id": 0, "pluginIngestEnabled": 1}) or {}
        return settings.get("pluginIngestEnabled") is not False

    def is_plugin_enabled_for_author(self, author: str) -> bool:
        if not self.get_plugin_ingest_enabled():
            return False

        author = self.resolve_author_alias(_normalize_author(author))
        profile = self.db.author_profiles.find_one({"rawAuthor": author}, {"pluginEnabled": 1})

        if profile and profile.get("pluginEnabled") is False:
            return False

        return True

    def get_effective_plugin_ingest_resume_cutoff_utc(self, author: str) -> dt.datetime | None:
        author = self.resolve_author_alias(_normalize_author(author))
        stamps: list[dt.datetime] = []
        plugins = self.db.system_settings.find_one({"kind": "plugins"}, {"_id": 0, "pluginIngestResumedAtUtc": 1}) or {}
        global_resume = _coerce_datetime(plugins.get("pluginIngestResumedAtUtc"))

        if global_resume is not None:
            stamps.append(global_resume)

        profile = self.db.author_profiles.find_one({"rawAuthor": author}, {"_id": 0, "pluginIngestResumedAtUtc": 1}) or {}
        author_resume = _coerce_datetime(profile.get("pluginIngestResumedAtUtc"))

        if author_resume is not None:
            stamps.append(author_resume)

        if not stamps:
            return None

        return max(stamps)

    def resolve_author_alias(self, raw_author: str | None) -> str:
        normalized_author = _normalize_author(raw_author or "Unknown User")
        alias = self.db.author_aliases.find_one({"sourceRawAuthor": normalized_author}, {"_id": 0, "targetRawAuthor": 1})

        if alias and alias.get("targetRawAuthor"):
            return _normalize_author(alias.get("targetRawAuthor"))

        return normalized_author

    def author_aliases(self) -> list[dict[str, Any]]:
        return list(self.db.author_aliases.find({}, {"_id": 0}).sort("sourceRawAuthor", ASCENDING))

    def upsert_author_alias(self, source_raw_author: str, target_raw_author: str) -> dict[str, Any]:
        source = _normalize_author(source_raw_author)
        target = _normalize_author(target_raw_author)

        if not source or not target:
            return {"ok": False, "error": "Source and target authors are required"}

        if source == target:
            return {"ok": False, "error": "Source and target authors must be different"}

        if target not in composed(self).list_authors():
            return {"ok": False, "error": "Target profile does not exist"}

        now = dt.datetime.now(dt.UTC)
        self.db.author_profiles.update_one(
            {"rawAuthor": target},
            {
                "$setOnInsert": {
                    "rawAuthor": target,
                    "displayName": target,
                    "team": "",
                    "pluginEnabled": True,
                    "authorColor": _author_color(target),
                    "createdAt": now,
                },
                "$set": {
                    "updatedAt": now,
                },
            },
            upsert=True,
        )
        self.db.author_aliases.update_one(
            {"sourceRawAuthor": source},
            {
                "$set": {
                    "sourceRawAuthor": source,
                    "targetRawAuthor": target,
                    "updatedAt": now,
                },
                "$setOnInsert": {
                    "createdAt": now,
                },
            },
            upsert=True,
        )
        self.db.author_profiles.delete_many({"rawAuthor": source})
        composed(self).rebuild_aggregates_for_author_dates([source, target])
        return {"ok": True, "alias": {"sourceRawAuthor": source, "targetRawAuthor": target}}

    def delete_author_alias(self, source_raw_author: str) -> dict[str, Any]:
        source = _normalize_author(source_raw_author)

        if not source:
            return {"ok": False, "error": "Source author is required"}

        result = self.db.author_aliases.delete_one({"sourceRawAuthor": source})
        composed(self).rebuild_aggregates_for_author_dates([source])
        return {"ok": True, "deleted": getattr(result, "deleted_count", 0)}

    def upsert_interval_settings(
        self,
        default_send_interval_seconds: int | None,
        idle_threshold_seconds: int | None,
        plugin_ingest_enabled: bool | None,
        author: str | None,
        author_send_interval_seconds: int | None,
        telegram_online_prompt_delay_minutes: int | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)

        global_update: dict[str, Any] = {"updatedAt": now}

        if default_send_interval_seconds is not None:
            global_update["sendIntervalSeconds"] = default_send_interval_seconds

        if idle_threshold_seconds is not None:
            global_update["idleThresholdSeconds"] = idle_threshold_seconds

        if telegram_online_prompt_delay_minutes is not None:
            clamped = max(
                1,
                min(int(telegram_online_prompt_delay_minutes), MAX_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES),
            )
            global_update["telegramOnlinePromptDelayMinutes"] = clamped

        if len(global_update) > 1:
            self.db.interval_settings.update_one(
                {"kind": "global"},
                {"$set": global_update},
                upsert=True,
            )

        if plugin_ingest_enabled is not None:
            prev_plugin_enabled = self.get_plugin_ingest_enabled()
            plugin_fields: dict[str, Any] = {
                "kind": "plugins",
                "pluginIngestEnabled": plugin_ingest_enabled,
                "updatedAt": now,
            }

            if plugin_ingest_enabled and not prev_plugin_enabled:
                plugin_fields["pluginIngestResumedAtUtc"] = now

            self.db.system_settings.update_one(
                {"kind": "plugins"},
                {"$set": plugin_fields},
                upsert=True,
            )

        if author and author_send_interval_seconds is not None:
            author = _normalize_author(author)
            self.db.interval_settings.update_one(
                {"kind": "author", "author": author},
                {
                    "$set": {
                        "author": author,
                        "sendIntervalSeconds": author_send_interval_seconds,
                        "updatedAt": now,
                    }
                },
                upsert=True,
            )

        composed(self).invalidate_activity_summary_cache()
        return self.get_interval_settings()

    def get_avatar_refresh_cadence(self) -> str:
        doc = self.db.system_settings.find_one({"kind": "avatars"}, {"_id": 0, "refreshCadence": 1}) or {}
        return normalize_avatar_refresh_cadence(doc.get("refreshCadence") or DEFAULT_AVATAR_REFRESH_CADENCE)

    def upsert_avatar_settings(self, refresh_cadence: str) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        cadence = normalize_avatar_refresh_cadence(refresh_cadence)
        self.db.system_settings.update_one(
            {"kind": "avatars"},
            {
                "$set": {
                    "kind": "avatars",
                    "refreshCadence": cadence,
                    "updatedAt": now,
                },
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )
        composed(self).invalidate_activity_summary_cache()
        return {"ok": True, "avatarRefreshCadence": cadence}

    def get_telegram_online_prompt_delay_seconds(self) -> int:
        settings = self.get_interval_settings()
        return int(settings["telegramOnlinePromptDelayMinutes"]) * 60

    def get_interval_settings(self) -> dict[str, Any]:
        global_setting = self.db.interval_settings.find_one({"kind": "global"}) or {}
        author_settings = list(
            self.db.interval_settings.find({"kind": "author"}, {"_id": 0}).sort("author", ASCENDING)
        )
        raw_minutes = global_setting.get("telegramOnlinePromptDelayMinutes")

        if raw_minutes is None:
            telegram_online_prompt_delay_minutes = DEFAULT_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES
        else:
            telegram_online_prompt_delay_minutes = max(
                1,
                min(int(raw_minutes), MAX_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES),
            )

        return {
            "defaultSendIntervalSeconds": int(
                global_setting.get("sendIntervalSeconds", composed(self).default_send_interval_seconds)
            ),
            "idleThresholdSeconds": int(global_setting.get("idleThresholdSeconds", DEFAULT_IDLE_THRESHOLD_SECONDS)),
            "pluginIngestEnabled": self.get_plugin_ingest_enabled(),
            "telegramOnlinePromptDelayMinutes": telegram_online_prompt_delay_minutes,
            "authors": author_settings,
            "avatarRefreshCadence": self.get_avatar_refresh_cadence(),
        }

    def get_server_stats(self) -> dict[str, Any]:
        usage = shutil.disk_usage("/")
        total = int(usage.total)
        used = int(usage.used)
        free = int(usage.free)
        percent = round((used / total) * 100, 1) if total else 0.0
        categories = [
            _server_stats_category(key, path)
            for key, path in SERVER_STATS_PATHS.items()
        ]
        known_bytes = sum(int(item["bytes"]) for item in categories)
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

        return {
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
        }

    def upsert_discord_settings(self, meeting_auto_afk_timeout_seconds: int) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        current = self.get_discord_settings()
        self.db.system_settings.update_one(
            {"kind": "discord"},
            {
                "$set": {
                    "kind": "discord",
                    "meetingAutoAfkTimeoutSeconds": meeting_auto_afk_timeout_seconds,
                    "meetingSummariesEnabled": current["meetingSummariesEnabled"],
                    "meetingSummaryMinParticipants": current["meetingSummaryMinParticipants"],
                    "meetingSummaryMinDurationSeconds": current["meetingSummaryMinDurationSeconds"],
                    "meetingSummaryLanguage": current["meetingSummaryLanguage"],
                    "meetingSummaryRecipient": current["meetingSummaryRecipient"],
                    "meetingAudioRetentionSeconds": current["meetingAudioRetentionSeconds"],
                    "meetingSummaryPrompt": current["meetingSummaryPrompt"],
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
        composed(self).invalidate_activity_summary_cache()
        return self.get_discord_settings()

    def upsert_discord_summary_settings(
        self,
        *,
        meeting_auto_afk_timeout_seconds: int,
        meeting_summaries_enabled: bool,
        meeting_summary_min_participants: int,
        meeting_summary_min_duration_seconds: int,
        meeting_summary_language: str,
        meeting_summary_recipient: str,
        meeting_audio_retention_seconds: int,
        meeting_summary_prompt: str,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.system_settings.update_one(
            {"kind": "discord"},
            {
                "$set": {
                    "kind": "discord",
                    "meetingAutoAfkTimeoutSeconds": meeting_auto_afk_timeout_seconds,
                    "meetingSummariesEnabled": meeting_summaries_enabled,
                    "meetingSummaryMinParticipants": meeting_summary_min_participants,
                    "meetingSummaryMinDurationSeconds": meeting_summary_min_duration_seconds,
                    "meetingSummaryLanguage": meeting_summary_language.strip() or "English",
                    "meetingSummaryRecipient": meeting_summary_recipient.strip() or "work_chat",
                    "meetingAudioRetentionSeconds": meeting_audio_retention_seconds,
                    "meetingSummaryPrompt": meeting_summary_prompt.strip() or DEFAULT_MEETING_SUMMARY_PROMPT,
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
        composed(self).invalidate_activity_summary_cache()
        return self.get_discord_settings()

    def get_discord_settings(self) -> dict[str, Any]:
        settings = self.db.system_settings.find_one({"kind": "discord"}) or {}
        return {
            "meetingAutoAfkTimeoutSeconds": int(
                settings.get("meetingAutoAfkTimeoutSeconds", DEFAULT_DISCORD_MEETING_AUTO_AFK_TIMEOUT_SECONDS)
            ),
            "meetingSummariesEnabled": bool(settings.get("meetingSummariesEnabled", False)),
            "meetingSummaryMinParticipants": int(settings.get("meetingSummaryMinParticipants", 2)),
            "meetingSummaryMinDurationSeconds": int(settings.get("meetingSummaryMinDurationSeconds", 120)),
            "meetingSummaryLanguage": str(settings.get("meetingSummaryLanguage") or "English"),
            "meetingSummaryRecipient": str(settings.get("meetingSummaryRecipient") or "work_chat"),
            "meetingAudioRetentionSeconds": int(settings.get("meetingAudioRetentionSeconds", 0)),
            "meetingSummaryPrompt": str(
                settings.get("meetingSummaryPrompt") or settings.get("meetingAudioEditPrompt") or DEFAULT_MEETING_SUMMARY_PROMPT
            ),
        }


