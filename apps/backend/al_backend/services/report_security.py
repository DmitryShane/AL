from __future__ import annotations

from ..activity_math import dt
from ..mongo_composable import MongoComposableMixin


class ReportSecurityService(MongoComposableMixin):
    def log_report_security_event(
        self,
        event_type: str,
        source: str,
        plugin_version: str | None = None,
        author: str | None = None,
        author_email: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        device_id: str | None = None,
        challenge_id: str | None = None,
        message: str | None = None,
    ) -> None:
        self.db.report_security_events.insert_one(
            {
                "type": event_type,
                "severity": "critical",
                "source": source,
                "pluginVersion": plugin_version or "",
                "author": author or "Unknown User",
                "authorEmail": author_email or "",
                "projectId": project_id or "",
                "sessionId": session_id or "",
                "deviceId": device_id or "",
                "challengeId": challenge_id or "",
                "message": message or "Suspicious report submission detected.",
                "createdAt": dt.datetime.now(dt.UTC),
            }
        )
