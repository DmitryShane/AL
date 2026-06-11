"""Typing bridge for mixin classes composed into BackendServices."""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable, ClassVar, Protocol, cast

from pymongo.database import Database


class BackendComposableHost(Protocol):
    """Structural supertype describing cross-shard helpers available at runtime on BackendServices."""

    aggregates_version: ClassVar[int]

    avatar_cache_dir: Any
    default_send_interval_seconds: int
    db: Database

    # Activity summary and report listing
    def _apply_live_activity_summary(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        telegram_seconds_by_author_date: dict[tuple[str, str], int],
        break_seconds_by_author_date: dict[tuple[str, str], int],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
        meeting_seconds_by_author_date: dict[tuple[str, str], int] | None = None,
        meeting_buckets: dict[tuple[str, str], list[dict[str, int]]] | None = None,
    ) -> None: ...

    def _apply_live_telegram_summary(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        telegram_seconds_by_author_date: dict[tuple[str, str], int],
        break_seconds_by_author_date: dict[tuple[str, str], int],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
        meeting_seconds_by_author_date: dict[tuple[str, str], int] | None = None,
        meeting_buckets: dict[tuple[str, str], list[dict[str, int]]] | None = None,
    ) -> None: ...

    def _apply_raw_event_to_aggregates(self, event: dict[str, Any]) -> dict[str, Any]: ...

    def _apply_snapshot_to_aggregates(self, snapshot: dict[str, Any]) -> None: ...

    def apply_vacation_mark_to_author(self, author: dict[str, Any], day_date: str) -> None: ...

    def convert_deltas_to_vacation_overtime(self, deltas: dict[str, Any]) -> dict[str, Any]: ...

    def convert_hourly_to_vacation_overtime(self, hourly_activity: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    def is_vacation_day(self, raw_author: str, day_date: str) -> bool: ...

    # Hourly enrichment helpers
    def _break_buckets_for_daily_items(
        self, daily_items: list[dict[str, Any]]
    ) -> dict[tuple[str, str], list[dict[str, int]]]: ...

    def _build_event_batch_report_rows(
        self,
        batch: dict[str, Any],
        delta_items: list[tuple[dict[str, Any], dict[str, Any]]],
        cutoff: dt.datetime | None = None,
    ) -> list[dict[str, Any]]: ...

    def _ensure_summary_author(
        self, authors_by_raw: dict[str, dict[str, Any]], raw_author: str, profiles: dict[str, dict[str, Any]]
    ) -> dict[str, Any]: ...

    # Workday, break, meeting, and status helpers
    def _close_break_session(self, normalized_telegram: str, raw_author: str, event_time: dt.datetime) -> dict[str, Any]: ...

    def _insert_discord_meeting_report_row(
        self,
        raw_author: str,
        discord_user_id: str,
        discord_username: str,
        event_type: str,
        event_time: dt.datetime,
        event_date: str,
        time_zone_id: str,
        received_at: dt.datetime,
        status: str,
        guild_id: str | None,
        channel_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def _insert_telegram_report_row(
        self,
        raw_author: str,
        telegram_username: str,
        event_type: str,
        event_time: dt.datetime,
        event_date: str,
        time_zone_id: str,
        received_at: dt.datetime,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def _materialize_status_report_rows(self) -> None: ...

    def materialize_live_meeting_reports(self, now: dt.datetime | None = None) -> int: ...

    def _meeting_buckets_for_daily_items(
        self, daily_items: list[dict[str, Any]], now: dt.datetime | None = None
    ) -> dict[tuple[str, str], list[dict[str, int]]]: ...

    def _profiles_by_raw_author(self) -> dict[str, dict[str, Any]]: ...

    def _record_status_transition_for_author(
        self,
        author: dict[str, Any],
        send_interval_seconds: int,
        now: dt.datetime,
        track_plugin_staleness: bool,
    ) -> None: ...

    def _schedule_telegram_break_activity_prompt_if_needed(
        self,
        raw_author: str,
        day_date: str,
        source: str,
        report_time: dt.datetime,
    ) -> None: ...

    def _schedule_telegram_online_prompt_if_needed(
        self, raw_author: str, day_date: str, source: str, received_at: dt.datetime
    ) -> None: ...

    def should_suppress_vacation_prompt(self, raw_author: str, day_date: str) -> bool: ...

    def vacation_mark_for_author_date(self, raw_author: str, day_date: str) -> dict[str, Any] | None: ...

    def vacation_overtime_window_for_event(self, event: dict[str, Any]) -> tuple[dt.datetime, dt.datetime] | None: ...

    def _should_materialize_aggregate_date(self, day_date: str, author: str | None = None) -> bool: ...

    def _telegram_gaps_for_daily_items(self, daily_items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]: ...

    def _update_meeting_recording_pipeline_status(
        self,
        recording_id: str,
        status: str,
        *,
        ended_at: dt.datetime | None = None,
        duration_seconds: int | None = None,
        updated_at: dt.datetime | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None: ...

    # Authors and settings
    def author_aliases(self) -> list[dict[str, Any]]: ...

    def author_alias_keys(self, raw_author: str | None) -> list[str]: ...

    def author_profiles(self) -> list[dict[str, Any]]: ...

    def device_profiles(self) -> list[dict[str, Any]]: ...

    def device_profile_changes(self, since: dt.datetime) -> list[dict[str, Any]]: ...

    def migrate_device_author_profiles(self) -> dict[str, Any]: ...

    def upsert_device_profile_alias(self, raw_device: str, target_raw_author: str) -> dict[str, Any]: ...

    def delete_device_profile(self, raw_device: str) -> dict[str, Any]: ...

    def delete_all_device_profiles(self) -> dict[str, Any]: ...

    def get_avatar_refresh_cadence(self) -> str: ...

    def get_discord_settings(self) -> dict[str, Any]: ...

    def get_meeting_notification_settings(self) -> dict[str, Any]: ...

    def fake_online_settings(self) -> dict[str, Any]: ...

    def delete_fake_online_settings(self, raw_author: str) -> dict[str, Any]: ...

    def upsert_fake_online_settings(
        self,
        raw_author: str,
        enabled: bool,
        days_of_week: list[int],
        start_time: str,
        end_time: str,
        delay_min_seconds: int,
        delay_max_seconds: int,
    ) -> dict[str, Any]: ...

    def claim_due_fake_online_prompts(self, now: dt.datetime | None = None) -> list[dict[str, Any]]: ...

    def get_effective_plugin_ingest_resume_cutoff_utc(self, author: str) -> dt.datetime | None: ...

    def get_idle_threshold_for_author(self, author: str, source: str | None = None) -> int: ...

    def get_interval_for_author(self, author: str, source: str | None = None) -> int: ...

    def get_plugin_ingest_enabled(self) -> bool: ...

    def is_deleted_author_profile(self, raw_author: str | None, author_email: str | None = None) -> bool: ...

    def get_telegram_online_prompt_delay_seconds(self) -> int: ...

    # Cache and aggregate maintenance
    def invalidate_activity_summary_cache(
        self, dates: list[str] | tuple[str, ...] | set[str] | None = None
    ) -> None: ...

    def list_authors(self) -> list[str]: ...

    def purge_editor_plugin_activity_for_author_day(self, raw_author: str, day_date: str) -> dict[str, Any]: ...

    def rebuild_aggregates_if_needed(
        self,
        force: bool = False,
        scope: str = "full",
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None: ...

    def rebuild_aggregates_for_author_dates(
        self,
        authors: list[str] | tuple[str, ...] | set[str],
    ) -> dict[str, Any]: ...

    def rebuild_aggregates_for_dates(
        self,
        start_date: str,
        end_date: str | None = None,
        authors: list[str] | tuple[str, ...] | set[str] | None = None,
        dates: list[str] | tuple[str, ...] | set[str] | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]: ...

    def resolve_author_alias(self, raw_author: str | None) -> str: ...

    def touch_last_raw_report_received_at(self, raw_author: str, received_at: dt.datetime) -> None: ...

    def update_author_email(self, raw_author: str, author_email: str | None) -> None: ...

    def update_author_time_zone(
        self, raw_author: str, time_zone_id: Any, time_zone_display_name: Any | None = None
    ) -> None: ...


def composed(shard: object) -> BackendComposableHost:
    """Narrow arbitrary mixin shards to BackendComposableHost for cross-shard method access."""
    return cast(BackendComposableHost, shard)
