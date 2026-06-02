from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed
from ..hourly_fill_rules import merge_hourly_activity


def _is_analytics_device_author(raw_author: str, raw_devices: set[str]) -> bool:
    normalized = str(raw_author or "").strip()
    return normalized in raw_devices or (normalized.startswith("Device") and normalized[6:].isdigit())


def _merge_analytics_day_doc(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("activeSeconds", "idleSeconds", "breakSeconds", "meetingSeconds", "overtimeActiveSeconds", "daySeconds"):
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0))

    if target.get("hourlyActivity"):
        merge_hourly_activity(target["hourlyActivity"], source.get("hourlyActivity", []))
    else:
        target["hourlyActivity"] = [dict(hour) for hour in source.get("hourlyActivity", [])]


class ActivitySummaryAnalyticsMixin:
    def analytics_summary(self, period: str = "7d") -> dict[str, Any]:
        year = dt.date.today().year
        profiles = composed(self)._profiles_by_raw_author()
        start_date = dt.date(year - 1, 12, 1).isoformat()
        end_date = dt.date(year, 12, 31).isoformat()
        docs = list(self.db.daily_author_activity.find(_date_query(start_date, end_date), {"_id": 0}))
        raw_devices = {
            str(item.get("rawAuthor") or "")
            for item in self.db.device_report_identities.find({}, {"_id": 0, "rawAuthor": 1})
            if item.get("rawAuthor")
        }
        alias_sources = {
            str(item.get("sourceRawAuthor") or "")
            for item in composed(self).author_aliases()
            if item.get("sourceRawAuthor")
        }
        authors = set(composed(self).list_authors())
        docs_by_author: dict[str, dict[str, dict[str, Any]]] = {}

        def analytics_author_allowed(raw_author: str) -> bool:
            profile = profiles.get(raw_author, {})
            return (
                raw_author
                and raw_author not in alias_sources
                and not _is_analytics_device_author(raw_author, raw_devices)
                and str(profile.get("profileType") or "person") != "publisher"
            )

        for item in docs:
            source_author = str(item.get("author") or "")
            if not source_author:
                continue

            raw_author = composed(self).resolve_author_alias(source_author)

            if analytics_author_allowed(raw_author):
                authors.add(raw_author)
                author_docs = docs_by_author.setdefault(raw_author, {})
                date_key = str(item.get("date") or "")
                if date_key in author_docs:
                    _merge_analytics_day_doc(author_docs[date_key], item)
                else:
                    author_docs[date_key] = dict(item)

        author_summaries = []

        for raw_author in sorted(authors):
            if not analytics_author_allowed(raw_author):
                continue

            profile = profiles.get(raw_author, {})
            author_docs = list(docs_by_author.get(raw_author, {}).values())
            author_summaries.append(
                {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail", ""),
                    "displayName": _display_name(raw_author, profile),
                    "team": profile.get("team", ""),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                    "avatarUrl": _cached_author_avatar_api_url(raw_author, _github_username_for_avatar_fetch(raw_author, profile), profile),
                    "months": _analytics_year_months(author_docs, year),
                }
            )

        return {
            "year": year,
            "authors": sorted(author_summaries, key=lambda item: item["displayName"].lower()),
        }
