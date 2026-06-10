from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed


class SettingsBootstrapMixin:
    def get_settings_bootstrap(self) -> dict[str, Any]:
        return {
            "authors": [],
            "reports": [],
            "intervalSettings": self.get_interval_settings(),
            "discordSettings": self.get_discord_settings(),
            "meetingNotificationSettings": self.get_meeting_notification_settings(),
            "activitySummary": {
                "totals": {
                    "daySeconds": 0,
                    "telegramDaySeconds": 0,
                    "pluginDaySeconds": 0,
                    "rawPluginDaySeconds": 0,
                    "telegramToFirstActivitySeconds": 0,
                    "activeSeconds": 0,
                    "idleSeconds": 0,
                    "meetingSeconds": 0,
                    "breakSeconds": 0,
                    "overtimeActiveSeconds": 0,
                },
                "authors": [],
                "profiles": composed(self).author_profiles(),
                "authorAliases": composed(self).author_aliases(),
                "activityMix": [],
                "savedPrefabs": [],
                "overtimeActivityMix": [],
                "overtimeSavedPrefabs": [],
                "hourlyActivityByAuthor": [],
            },
        }
