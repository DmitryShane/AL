from __future__ import annotations

from .repositories.auth import AuthRepository
from .repositories.settings import SettingsRepository
from .repositories.authors import AuthorRepository
from .services.report_ingest import ReportIngestService
from .services.activity_summary import ActivitySummaryService
from .services.author_status_events import AuthorStatusEventsService
from .services.calendar import CalendarService
from .services.discord_meetings import DiscordMeetingService
from .services.telegram_activity import TelegramActivityService
from .services.activity_aggregation import ActivityAggregationService
from .storage import MongoStorage
from .settings import Settings
from .indexes import IndexManager


class BackendServices(
    AuthRepository,
    SettingsRepository,
    AuthorRepository,
    ReportIngestService,
    ActivitySummaryService,
    CalendarService,
    DiscordMeetingService,
    TelegramActivityService,
    AuthorStatusEventsService,
    ActivityAggregationService,
):
    aggregates_version = 28

    def __init__(self, storage: MongoStorage, settings: Settings):
        self.storage = storage
        self.client = storage.client
        self.db = storage.db
        self.default_send_interval_seconds = settings.default_send_interval_seconds
        self.avatar_cache_dir = settings.avatar_cache_dir

    def ping(self) -> bool:
        self.client.admin.command("ping")
        return True


class BackendContainer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = MongoStorage(settings)
        self.indexes = IndexManager(self.storage.db)
        self.services = BackendServices(self.storage, settings)
        self.auth = self.services
        self.settings_service = self.services
        self.authors = self.services
        self.report_ingest = self.services
        self.activity_aggregation = self.services
        self.activity_summary = self.services
        self.calendar = self.services
        self.telegram_activity = self.services
        self.discord_meetings = self.services

    def startup(self) -> None:
        self.indexes.ensure_indexes()
        self.activity_aggregation.rebuild_aggregates_if_needed()
        self.auth.ensure_bootstrap_site_admin(self.settings.admin_email, self.settings.admin_password)

    def close(self) -> None:
        self.storage.client.close()
