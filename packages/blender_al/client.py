from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .alr1 import encrypt_payload


def get_plugin_config(base_url: str, source: str, author: str, author_email: str, project_id: str) -> dict:
    query = urllib.parse.urlencode(
        {
            "source": source,
            "author": author,
            "authorEmail": author_email,
            "projectId": project_id,
        }
    )
    return _json_request(f"{base_url.rstrip('/')}/api/v1/plugins/config?{query}", method="GET")


def submit_event_batch(base_url: str, payload: dict, plugin_version: str, device_id: str) -> dict:
    base = base_url.rstrip("/")
    challenge = _json_request(
        f"{base}/api/v1/reports/challenge",
        method="POST",
        payload={
            "source": payload.get("source", "bal"),
            "pluginVersion": plugin_version,
            "author": payload.get("author") or "Unknown User",
            "authorEmail": payload.get("authorEmail") or "",
            "projectId": payload.get("projectId") or "",
            "sessionId": payload.get("sessionId") or "",
            "deviceId": device_id,
        },
    )
    encrypted_packet = encrypt_payload(payload, challenge["publicModulus"], challenge["publicExponent"])
    return _json_request(
        f"{base}/api/v1/reports",
        method="POST",
        payload={
            "source": payload.get("source", "bal"),
            "pluginVersion": plugin_version,
            "challengeId": challenge["challengeId"],
            "deviceId": device_id,
            "encryptedPacket": encrypted_packet,
        },
    )


def _json_request(url: str, method: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    if not body:
        return {}

    return json.loads(body)
