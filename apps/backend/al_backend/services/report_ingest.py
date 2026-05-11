from __future__ import annotations

import hashlib

from ..activity_math import *
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


def is_unknown_device_author(value: Any) -> bool:
    author = _normalize_author(value or "")
    return author in {"Unknown User", "Device"}


def _device_report_id_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_valid_device_id(value: Any) -> bool:
    normalized = str(value or "").strip()
    return bool(normalized) and normalized != "00000000-0000-0000-0000-000000000000"


def _device_fallback_id_from_payload(payload: dict[str, Any]) -> str:
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue

        metadata = event.get("metadata") or {}

        if not isinstance(metadata, dict):
            continue

        fallback_id = str(metadata.get("deviceFallbackId") or "").strip()

        if _is_valid_device_id(fallback_id):
            return fallback_id

    return ""


class ReportIngestService(MongoComposableMixin):
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
        effective_source = device_source_from_payload(payload, source) if source == "dev" else source
        resolved_device_id = str(device_id or payload.get("deviceId") or "")

        if is_device_source(effective_source) and not _is_valid_device_id(resolved_device_id):
            resolved_device_id = _device_fallback_id_from_payload(payload)

        if resolved_device_id:
            payload["deviceId"] = resolved_device_id

        if is_device_source(effective_source) and self.is_unknown_device_author(payload.get("author")):
            payload["author"] = self.resolve_device_report_author(effective_source, resolved_device_id)

        original_author = _normalize_author(payload.get("author") or "Unknown User")
        payload["author"] = composed(self).resolve_author_alias(original_author)
        normalized_time_zone = _author_configured_time_zone_id(payload["author"]) or _valid_time_zone_id(payload.get("timeZoneId"))

        if normalized_time_zone:
            payload["timeZoneId"] = normalized_time_zone
            payload["timeZoneDisplayName"] = str(payload.get("timeZoneDisplayName") or "").strip() or normalized_time_zone

        if is_device_source(effective_source):
            self.touch_device_report_identity(effective_source, resolved_device_id, payload, plugin_version, now)

        if not self._is_unassigned_device_report_author(effective_source, payload.get("author")):
            composed(self).update_author_time_zone(str(payload.get("author") or "Unknown User"), payload.get("timeZoneId"), payload.get("timeZoneDisplayName"))
        report_type = self.consume_expected_report_type(payload.get("author"))
        raw_result = self.db.raw_reports.insert_one(
            {
                "source": effective_source,
                "pluginVersion": plugin_version,
                "challengeId": challenge_id,
                "deviceId": resolved_device_id,
                "encryptedPacket": encrypted_packet,
                "receivedAt": now,
                "status": "decoded",
                "reportType": report_type,
            }
        )
        author_for_stale_touch = str(payload.get("author") or "Unknown User")

        if isinstance(payload.get("events"), list):
            meaningful_for_stale = self._save_event_batch(
                source=effective_source,
                plugin_version=plugin_version,
                payload=payload,
                raw_report_id=raw_result.inserted_id,
                report_type=report_type,
                received_at=now,
                challenge_id=challenge_id,
                device_id=resolved_device_id,
            )
            if meaningful_for_stale and not self._is_unassigned_device_report_author(effective_source, author_for_stale_touch):
                composed(self).touch_last_raw_report_received_at(author_for_stale_touch, now)
            composed(self).invalidate_activity_summary_cache()
            return str(raw_result.inserted_id)

        snapshot = dict(payload)
        snapshot.update(
            {
                "source": effective_source,
                "pluginVersion": plugin_version,
                "rawReportId": raw_result.inserted_id,
                "receivedAt": now,
                "reportType": report_type,
                "challengeId": challenge_id,
                "deviceId": resolved_device_id,
            }
        )
        self.db.activity_snapshots.insert_one(snapshot)
        composed(self)._apply_snapshot_to_aggregates(snapshot)
        if not self._is_unassigned_device_report_author(effective_source, author_for_stale_touch):
            composed(self).touch_last_raw_report_received_at(author_for_stale_touch, now)
        composed(self).invalidate_activity_summary_cache()
        return str(raw_result.inserted_id)

    def _is_unassigned_device_report_author(self, source: str, author: Any) -> bool:
        if not is_device_source(source):
            return False

        normalized = _normalize_author(author)

        if not normalized:
            return False

        return bool(self.db.device_report_identities.find_one({"rawAuthor": normalized}, {"_id": 1}))

    def touch_device_report_identity(
        self,
        source: str,
        device_id: str,
        payload: dict[str, Any],
        plugin_version: str,
        received_at: dt.datetime,
    ) -> None:
        normalized_device_id = str(device_id or "").strip()

        if not _is_valid_device_id(normalized_device_id):
            return

        metadata = self._latest_device_event_metadata(payload)
        device_sent_at = str(payload.get("sentAt") or "").strip()
        time_zone_id = str(payload.get("timeZoneId") or "").strip()
        time_zone_display_name = str(payload.get("timeZoneDisplayName") or "").strip()
        first_touch_fields = {
            "firstSeenDeviceSentAt": device_sent_at,
            "firstSeenTimeZoneId": time_zone_id,
            "firstSeenTimeZoneDisplayName": time_zone_display_name,
        }
        first_touch_fields = {key: value for key, value in first_touch_fields.items() if value}
        self.db.device_report_identities.update_one(
            {"deviceIdHash": _device_report_id_hash(normalized_device_id)},
            {
                "$set": {
                    "source": source,
                    "lastDeviceId": normalized_device_id,
                    "lastProjectId": str(payload.get("projectId") or ""),
                    "lastPluginVersion": plugin_version,
                    "lastSeenAt": received_at,
                    "lastDeviceSentAt": device_sent_at,
                    "lastTimeZoneId": time_zone_id,
                    "lastTimeZoneDisplayName": time_zone_display_name,
                    "lastMetadata": metadata,
                }
            },
        )
        if first_touch_fields:
            self.db.device_report_identities.update_one(
                {
                    "deviceIdHash": _device_report_id_hash(normalized_device_id),
                    "firstSeenDeviceSentAt": {"$exists": False},
                },
                {"$set": first_touch_fields},
            )

    def _latest_device_event_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        events = payload.get("events")

        if not isinstance(events, list):
            return {}

        latest_event = None

        for event in events:
            if not isinstance(event, dict):
                continue

            if latest_event is None:
                latest_event = event
                continue

            current_time = str(event.get("occurredAtUtc") or event.get("occurredAtLocal") or "")
            latest_time = str(latest_event.get("occurredAtUtc") or latest_event.get("occurredAtLocal") or "")

            if current_time >= latest_time:
                latest_event = event

        metadata = (latest_event or {}).get("metadata")
        return metadata if isinstance(metadata, dict) else {}

    def is_unknown_device_author(self, value: Any) -> bool:
        return is_unknown_device_author(value)

    def resolve_device_report_author(self, source: str, device_id: str) -> str:
        normalized_device_id = str(device_id or "").strip()

        if not _is_valid_device_id(normalized_device_id):
            return "Device"

        device_id_hash = _device_report_id_hash(normalized_device_id)
        existing = self.db.device_report_identities.find_one({"deviceIdHash": device_id_hash})

        if existing and existing.get("rawAuthor"):
            return str(existing["rawAuthor"])

        next_number = self.db.device_report_identities.count_documents({}) + 1
        raw_author = f"Device{next_number}"
        self.db.device_report_identities.update_one(
            {"deviceIdHash": device_id_hash},
            {
                "$setOnInsert": {
                    "source": source,
                    "deviceIdHash": device_id_hash,
                    "rawAuthor": raw_author,
                    "createdAt": dt.datetime.now(dt.UTC),
                }
            },
            upsert=True,
        )
        inserted = self.db.device_report_identities.find_one({"deviceIdHash": device_id_hash})
        return str((inserted or {}).get("rawAuthor") or raw_author)

    def _is_heartbeat_only_event_payload(self, payload: dict[str, Any]) -> bool:
        events = payload.get("events")

        if not isinstance(events, list) or not events:
            return False

        return all(isinstance(event, dict) and str(event.get("eventType") or "").strip() == "heartbeat" for event in events)

    def request_report_refresh(self, author: str | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        authors = [author] if author else composed(self).list_authors()

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
    ) -> bool:
        author = composed(self).resolve_author_alias(str(payload.get("author") or "Unknown User"))
        author_email = str(payload.get("authorEmail") or "")
        project_id = str(payload.get("projectId") or "")
        session_id = str(payload.get("sessionId") or "")
        resolved_device_id = str(device_id or payload.get("deviceId") or "")
        batch_id = _new_id()
        batch_delta_items: list[tuple[dict[str, Any], dict[str, Any]]] = []

        composed(self).update_author_email(author, author_email)
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
            "timeZoneId": payload.get("timeZoneId"),
            "timeZoneDisplayName": payload.get("timeZoneDisplayName"),
            "eventCount": len(payload.get("events") or []),
            "reportType": report_type,
        }
        self.db.raw_event_batches.insert_one(batch)

        events = sorted(payload.get("events") or [], key=lambda item: str(item.get("occurredAtUtc") or item.get("occurredAtLocal") or ""))
        resume_cutoff = composed(self).get_effective_plugin_ingest_resume_cutoff_utc(author)

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

            if resume_cutoff is not None:
                event_time = _raw_event_time(event)

                if event_time is None or event_time < resume_cutoff:
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

            deltas = composed(self)._apply_raw_event_to_aggregates(event)
            batch_delta_items.append((event, deltas))

        rows = self._build_event_batch_report_rows(batch, batch_delta_items)

        if not rows:
            return False

        _insert_many_if_supported(self.db.report_rows, rows)

        for row in rows:
            report_time = _report_row_time(row) or received_at
            row_date = str(row.get("date") or "")
            suppress_vacation_prompt = composed(self).should_suppress_vacation_prompt(author, row_date)

            if _has_active_or_overtime_delta(row) and not suppress_vacation_prompt:
                composed(self)._schedule_telegram_break_activity_prompt_if_needed(author, row_date, source, report_time)

            if not suppress_vacation_prompt:
                composed(self)._schedule_telegram_online_prompt_if_needed(author, row_date, source, received_at)

        return True

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
