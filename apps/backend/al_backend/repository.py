from __future__ import annotations

from .activity_math import *
from .repositories.auth import AuthRepositoryMixin
from .repositories.settings import SettingsRepositoryMixin
from .repositories.authors import AuthorRepositoryMixin
from .services.report_ingest import ReportIngestServiceMixin
from .services.activity_summary import ActivitySummaryServiceMixin
from .services.calendar import CalendarServiceMixin
from .services.discord_meetings import DiscordMeetingServiceMixin
from .services.telegram_activity import TelegramActivityServiceMixin
from .services.activity_aggregation import ActivityAggregationServiceMixin
from .storage import MongoStorage, StorageMixin
from .settings import Settings


class Repository(
    StorageMixin,
    AuthRepositoryMixin,
    SettingsRepositoryMixin,
    AuthorRepositoryMixin,
    ReportIngestServiceMixin,
    ActivitySummaryServiceMixin,
    CalendarServiceMixin,
    DiscordMeetingServiceMixin,
    TelegramActivityServiceMixin,
    ActivityAggregationServiceMixin,
):
    aggregates_version = 28

    def __init__(self, settings: Settings):
        self.storage = MongoStorage(settings)
        self.client = self.storage.client
        self.db = self.storage.db
        self.default_send_interval_seconds = settings.default_send_interval_seconds
