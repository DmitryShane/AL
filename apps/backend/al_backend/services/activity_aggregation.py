from __future__ import annotations

from typing import Any

from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin
from ..overtime_rules import OvertimeRuleContext
from .activity_aggregation_rebuild import ActivityAggregationRebuildMixin
from .activity_raw_event_accounting import ActivityRawEventAccountingMixin


class ActivityAggregationService(
    ActivityAggregationRebuildMixin,
    ActivityRawEventAccountingMixin,
    MongoComposableMixin,
):
    def _overtime_rule_context(self) -> OvertimeRuleContext:
        return OvertimeRuleContext(
            vacation_overtime_window_for_event=lambda event: composed(self).vacation_overtime_window_for_event(event),
            is_author_offline_after_latest_telegram_state=self._is_author_offline_after_latest_telegram_state,
            day_session_for_author_date=self._day_session_for_overtime_rules,
        )

    def _day_session_for_overtime_rules(self, raw_author: str, day_date: str) -> dict[str, Any] | None:
        return self.db.day_sessions.find_one(
            {"rawAuthor": raw_author, "date": day_date},
            {"_id": 0, "lastOfflineAt": 1, "timeZoneId": 1, "reminderAction": 1},
        )

    def _notifications_suppressed_for_rebuild(self) -> bool:
        return bool(getattr(self, "_suppress_rebuild_notification_side_effects", False))
