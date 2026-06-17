from __future__ import annotations

import logging
import threading

from .repositories.auth import AuthRepository
from .repositories.settings import SettingsRepository
from .repositories.authors import AuthorRepository
from .repositories.device_profiles import DeviceProfileRepository
from .services.author_avatar_service import AuthorAvatarService
from .services.author_timezone_rebucket import AuthorTimezoneRebucketService
from .services.report_challenges import ReportChallengeService
from .services.report_security import ReportSecurityService
from .services.report_chunks import ReportChunkService
from .services.report_ingest import ReportIngestService
from .services.report_listing import ReportListingService
from .services.activity_summary import ActivitySummaryService
from .services.author_status_events import AuthorStatusEventsService
from .services.publisher_profiles import PublisherProfileService
from .services.activity_live_summary import ActivityLiveSummaryService
from .services.editor_activity_purge import EditorActivityPurgeService
from .services.calendar_day_overrides import CalendarDayOverrideService
from .services.calendar import CalendarService
from .services.discord_meetings import DiscordMeetingService
from .services.telegram_meeting_delivery import TelegramMeetingDeliveryService
from .services.telegram_activity import TelegramActivityService
from .services.break_sessions import BreakSessionService
from .services.activity_aggregation import ActivityAggregationService
from .local_live_state_guard import close_imported_open_live_states
from .rebuild_jobs import mark_running_rebuild_jobs_interrupted
from .storage import MongoStorage
from .settings import Settings
from .indexes import IndexManager


logger = logging.getLogger("al_backend")


class BackendServices(
    AuthRepository,
    SettingsRepository,
    AuthorAvatarService,
    AuthorTimezoneRebucketService,
    ReportChallengeService,
    ReportSecurityService,
    ReportChunkService,
    AuthorRepository,
    DeviceProfileRepository,
    ReportIngestService,
    ReportListingService,
    EditorActivityPurgeService,
    ActivityLiveSummaryService,
    CalendarDayOverrideService,
    ActivitySummaryService,
    CalendarService,
    DiscordMeetingService,
    TelegramMeetingDeliveryService,
    BreakSessionService,
    TelegramActivityService,
    AuthorStatusEventsService,
    PublisherProfileService,
    ActivityAggregationService,
):
    aggregates_version = 30

    def __init__(self, storage: MongoStorage, settings: Settings):
        self.storage = storage
        self.client = storage.client
        self.db = storage.db
        self.default_send_interval_seconds = settings.default_send_interval_seconds
        self.avatar_cache_dir = settings.avatar_cache_dir
        self.aggregate_version_rebuild_scope = settings.aggregate_version_rebuild_scope
        self.openai_usage_api_key = settings.openai_usage_api_key
        self.openai_usage_project_id = settings.openai_usage_project_id
        self.activity_snapshot_maintenance_lock = threading.Lock()

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
        mark_running_rebuild_jobs_interrupted(self.services)
        close_imported_open_live_states(self.services)
        self.activity_aggregation.rebuild_aggregates_if_needed(scope=self.activity_aggregation.aggregate_version_rebuild_scope)
        try:
            self.activity_summary.start_activity_snapshot_scheduler()
        except Exception:
            logger.exception("Activity snapshot maintenance failed during startup")
        try:
            self.settings_service.start_server_stats_daily_refresh()
        except Exception:
            logger.exception("Server stats scheduler failed during startup")
        self.auth.ensure_bootstrap_site_admin(self.settings.admin_email, self.settings.admin_password)

    def close(self) -> None:
        try:
            self.activity_summary.stop_activity_snapshot_scheduler()
        except Exception:
            logger.exception("Activity snapshot scheduler shutdown failed")
        try:
            self.settings_service.stop_server_stats_daily_refresh()
        except Exception:
            logger.exception("Server stats scheduler shutdown failed")
        self.storage.client.close()
