from __future__ import annotations

from typing import Any

from pymongo import ReturnDocument

from ..activity_math import REPORT_CHALLENGE_TTL_SECONDS, dt, _new_id, _normalize_author
from ..mongo_composable import MongoComposableMixin


class ReportChallengeService(MongoComposableMixin):
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
