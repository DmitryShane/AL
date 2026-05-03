from __future__ import annotations

from ..activity_math import *


class ReportIngestService:
    def create_report_challenge(self, challenge_in: Any, keys: Any) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        expires_at = now + dt.timedelta(seconds=REPORT_CHALLENGE_TTL_SECONDS)
        challenge_id = _new_id()
        challenge = {
            "challengeId": challenge_id,
            "source": challenge_in.source,
            "pluginVersion": challenge_in.plugin_version,
            "author": _normalize_author(challenge_in.author),
            "authorEmail": challenge_in.author_email or "",
            "projectId": challenge_in.project_id or "",
            "sessionId": challenge_in.session_id or "",
            "deviceId": challenge_in.device_id or "",
            "privateKeyPem": keys.private_key_pem,
            "publicModulus": keys.public_modulus,
            "publicExponent": keys.public_exponent,
            "createdAt": now,
            "expiresAt": expires_at,
        }
        self.db.report_challenges.insert_one(challenge)
        return {**challenge, "expiresAt": expires_at.isoformat()}

    def claim_report_challenge(self, challenge_id: str, source: str, device_id: str | None) -> dict[str, Any] | None:
        now = dt.datetime.now(dt.UTC)
        query: dict[str, Any] = {
            "challengeId": challenge_id,
            "source": source,
            "expiresAt": {"$gt": now},
            "consumedAt": {"$exists": False},
        }

        if device_id:
            query["deviceId"] = {"$in": [device_id, ""]}

        return self.db.report_challenges.find_one_and_update(
            query,
            {"$set": {"consumedAt": now}},
            return_document=ReturnDocument.AFTER,
        )

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

    def save_report(
        self,
        source: str,
        plugin_version: str,
        encrypted_packet: str,
        payload: dict[str, Any],
        challenge_id: str,
        device_id: str | None = None,
    ) -> str:
        now = dt.datetime.now(dt.UTC)
        payload = dict(payload)
        original_author = _normalize_author(payload.get("author") or "Unknown User")
        payload["author"] = self.resolve_author_alias(original_author)
        normalized_time_zone = _author_configured_time_zone_id(payload["author"]) or _valid_time_zone_id(payload.get("timeZoneId"))

        if normalized_time_zone:
            payload["timeZoneId"] = normalized_time_zone
            payload["timeZoneDisplayName"] = str(payload.get("timeZoneDisplayName") or "").strip() or normalized_time_zone

        self.update_author_time_zone(payload.get("author"), payload.get("timeZoneId"), payload.get("timeZoneDisplayName"))
        report_type = self.consume_expected_report_type(payload.get("author"))
        raw_result = self.db.raw_reports.insert_one(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "challengeId": challenge_id,
                "deviceId": device_id or payload.get("deviceId", ""),
                "encryptedPacket": encrypted_packet,
                "receivedAt": now,
                "status": "decoded",
                "reportType": report_type,
            }
        )

        if isinstance(payload.get("events"), list):
            self._save_event_batch(
                source=source,
                plugin_version=plugin_version,
                payload=payload,
                raw_report_id=raw_result.inserted_id,
                report_type=report_type,
                received_at=now,
                challenge_id=challenge_id,
                device_id=device_id,
            )
            return str(raw_result.inserted_id)

        snapshot = dict(payload)
        snapshot.update(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "rawReportId": raw_result.inserted_id,
                "receivedAt": now,
                "reportType": report_type,
                "challengeId": challenge_id,
                "deviceId": device_id or payload.get("deviceId", ""),
            }
        )
        self.db.activity_snapshots.insert_one(snapshot)
        self._apply_snapshot_to_aggregates(snapshot)
        return str(raw_result.inserted_id)

    def request_report_refresh(self, author: str | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        authors = [author] if author else self.list_authors()

        for item in authors:
            self.db.report_refresh_requests.update_one(
                {"author": item},
                {"$set": {"author": item, "requestedAt": now}},
                upsert=True,
            )

        return {
            "ok": True,
            "requestedAuthors": authors,
            "requestedCount": len(authors),
        }

    def should_submit_report_now(self, author: str) -> bool:
        request = self.db.report_refresh_requests.find_one_and_delete({"author": author})
        if request:
            self.db.manual_report_expectations.update_one(
                {"author": author},
                {"$set": {"author": author, "requestedAt": request.get("requestedAt", dt.datetime.now(dt.UTC))}},
                upsert=True,
            )
        return request is not None

    def consume_expected_report_type(self, author: str | None) -> str:
        if not author:
            return "auto"

        result = self.db.manual_report_expectations.find_one_and_delete({"author": author})

        if result:
            return "manual"

        return "auto"

    def _save_event_batch(
        self,
        source: str,
        plugin_version: str,
        payload: dict[str, Any],
        raw_report_id: Any,
        report_type: str,
        received_at: dt.datetime,
        challenge_id: str,
        device_id: str | None,
    ) -> None:
        author = self.resolve_author_alias(str(payload.get("author") or "Unknown User"))
        author_email = str(payload.get("authorEmail") or "")
        project_id = str(payload.get("projectId") or "")
        session_id = str(payload.get("sessionId") or "")
        resolved_device_id = str(device_id or payload.get("deviceId") or "")
        batch_id = _new_id()
        batch_delta_items: list[tuple[dict[str, Any], dict[str, Any]]] = []

        self.update_author_email(author, author_email)
        batch = {
            "batchId": batch_id,
            "rawReportId": raw_report_id,
            "challengeId": challenge_id,
            "source": source,
            "pluginVersion": plugin_version,
            "author": author,
            "authorEmail": author_email,
            "projectId": project_id,
            "sessionId": session_id,
            "deviceId": resolved_device_id,
            "receivedAt": received_at,
            "sentAt": payload.get("sentAt"),
            "eventCount": len(payload.get("events") or []),
            "reportType": report_type,
        }
        self.db.raw_event_batches.insert_one(batch)

        events = sorted(payload.get("events") or [], key=lambda item: str(item.get("occurredAtUtc") or item.get("occurredAtLocal") or ""))

        for raw_event in events:
            event = _normalize_raw_event(
                raw_event,
                source=source,
                plugin_version=plugin_version,
                author=author,
                author_email=author_email,
                project_id=project_id,
                session_id=session_id,
                device_id=resolved_device_id,
                batch_id=batch_id,
                raw_report_id=raw_report_id,
                received_at=received_at,
                report_type=report_type,
                time_zone_id=payload.get("timeZoneId"),
                time_zone_display_name=payload.get("timeZoneDisplayName"),
            )

            if not event:
                continue

            try:
                self.db.raw_activity_events.insert_one(event)
            except DuplicateKeyError:
                self.log_report_security_event(
                    event_type="duplicate_event",
                    source=source,
                    plugin_version=plugin_version,
                    author=author,
                    author_email=author_email,
                    project_id=project_id,
                    session_id=session_id,
                    device_id=resolved_device_id,
                    challenge_id=challenge_id,
                    message="Raw event id was submitted more than once.",
                )
                continue

            deltas = self._apply_raw_event_to_aggregates(event)
            batch_delta_items.append((event, deltas))

        rows = self._build_event_batch_report_rows(batch, batch_delta_items)

        if not rows:
            return

        _insert_many_if_supported(self.db.report_rows, rows)

        for row in rows:
            report_time = _report_row_time(row) or received_at

            if _has_active_or_overtime_delta(row):
                self._schedule_telegram_break_activity_prompt_if_needed(author, str(row.get("date") or ""), source, report_time)

            self._schedule_telegram_online_prompt_if_needed(author, str(row.get("date") or ""), source, received_at)

    def _build_event_batch_report_rows(
        self,
        batch: dict[str, Any],
        delta_items: list[tuple[dict[str, Any], dict[str, Any]]],
        cutoff: dt.datetime | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        merged_items = _merge_event_delta_items_by_date(delta_items, cutoff)

        for batch_deltas, last_event in merged_items:
            if not last_event or not _has_time_delta(batch_deltas):
                continue

            rows.append(
                {
                    "source": batch.get("source"),
                    "pluginVersion": batch.get("pluginVersion"),
                    "author": batch.get("author") or "Unknown User",
                    "authorEmail": batch.get("authorEmail", ""),
                    "projectId": batch.get("projectId") or "",
                    "sessionId": batch.get("sessionId") or "",
                    "deviceId": batch.get("deviceId") or "",
                    "date": last_event.get("date"),
                    "recordedAt": last_event.get("occurredAtLocal") or last_event.get("occurredAtUtc"),
                    "receivedAt": batch.get("receivedAt"),
                    "lastRecordedAt": last_event.get("occurredAtLocal") or last_event.get("occurredAtUtc"),
                    "lastReceivedAt": batch.get("receivedAt"),
                    "timeZoneId": last_event.get("timeZoneId"),
                    "timeZoneDisplayName": last_event.get("timeZoneDisplayName"),
                    "rawReportId": batch.get("rawReportId"),
                    "batchId": batch.get("batchId"),
                    "challengeId": batch.get("challengeId"),
                    "reportType": batch.get("reportType", "auto"),
                    **batch_deltas,
                }
            )

        return rows


