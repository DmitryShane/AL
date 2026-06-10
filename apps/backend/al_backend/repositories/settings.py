from __future__ import annotations

from ..mongo_composable import MongoComposableMixin
from ..services.author_redirects import AuthorRedirectsMixin
from ..services.avatar_settings import AvatarSettingsMixin
from ..services.discord_settings import DiscordSettingsMixin
from ..services.fake_online_settings import FakeOnlineSettingsMixin
from ..services.meeting_notification_settings import MeetingNotificationSettingsMixin
from ..services.meeting_summary_settings import MeetingSummarySettingsMixin
from ..services.openai_usage_stats import OpenAIUsageStatsMixin
from ..services.send_interval_settings import SendIntervalSettingsMixin
from ..services.server_stats import ServerStatsServiceMixin
from ..services.settings_bootstrap import SettingsBootstrapMixin
from ..services.telegram_settings import TelegramSettingsMixin


class SettingsRepository(
    OpenAIUsageStatsMixin,
    SendIntervalSettingsMixin,
    TelegramSettingsMixin,
    AvatarSettingsMixin,
    AuthorRedirectsMixin,
    FakeOnlineSettingsMixin,
    DiscordSettingsMixin,
    MeetingSummarySettingsMixin,
    MeetingNotificationSettingsMixin,
    SettingsBootstrapMixin,
    ServerStatsServiceMixin,
    MongoComposableMixin,
):
    pass
