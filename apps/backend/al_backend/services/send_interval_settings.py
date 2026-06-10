from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed


class SendIntervalSettingsMixin:
    def get_interval_for_author(self, author: str, source: str | None = None) -> int:
        global_setting = self.db.interval_settings.find_one({"kind": "global"})

        if is_device_source(source) and global_setting and global_setting.get("deviceSendIntervalSeconds"):
            return int(global_setting["deviceSendIntervalSeconds"])

        if global_setting and global_setting.get("sendIntervalSeconds"):
            return int(global_setting["sendIntervalSeconds"])

        return composed(self).default_send_interval_seconds

    def get_idle_threshold_for_author(self, author: str, source: str | None = None) -> int:
        global_setting = self.db.interval_settings.find_one({"kind": "global"}) or {}

        if is_device_source(source) and global_setting.get("deviceIdleThresholdSeconds"):
            return int(global_setting["deviceIdleThresholdSeconds"])

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

        if composed(self).is_deleted_author_profile(author):
            return False

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

    def upsert_interval_settings(
        self,
        default_send_interval_seconds: int | None,
        device_send_interval_seconds: int | None = None,
        idle_threshold_seconds: int | None = None,
        device_idle_threshold_seconds: int | None = None,
        plugin_ingest_enabled: bool | None = None,
        telegram_online_prompt_delay_minutes: int | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)

        global_update: dict[str, Any] = {"updatedAt": now}

        if default_send_interval_seconds is not None:
            global_update["sendIntervalSeconds"] = default_send_interval_seconds

        if device_send_interval_seconds is not None:
            global_update["deviceSendIntervalSeconds"] = device_send_interval_seconds

        if idle_threshold_seconds is not None:
            global_update["idleThresholdSeconds"] = idle_threshold_seconds

        if device_idle_threshold_seconds is not None:
            global_update["deviceIdleThresholdSeconds"] = device_idle_threshold_seconds

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

        composed(self).invalidate_activity_summary_cache()
        return self.get_interval_settings()

    def get_interval_settings(self) -> dict[str, Any]:
        global_setting = self.db.interval_settings.find_one({"kind": "global"}) or {}
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
            "deviceSendIntervalSeconds": int(
                global_setting.get(
                    "deviceSendIntervalSeconds",
                    global_setting.get("sendIntervalSeconds", composed(self).default_send_interval_seconds),
                )
            ),
            "idleThresholdSeconds": int(global_setting.get("idleThresholdSeconds", DEFAULT_IDLE_THRESHOLD_SECONDS)),
            "deviceIdleThresholdSeconds": int(global_setting.get("deviceIdleThresholdSeconds", DEFAULT_IDLE_THRESHOLD_SECONDS)),
            "pluginIngestEnabled": self.get_plugin_ingest_enabled(),
            "telegramOnlinePromptDelayMinutes": telegram_online_prompt_delay_minutes,
            "avatarRefreshCadence": self.get_avatar_refresh_cadence(),
        }
