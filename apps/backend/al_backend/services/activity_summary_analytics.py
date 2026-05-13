from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed


class ActivitySummaryAnalyticsMixin:
    def analytics_summary(self, period: str = "7d") -> dict[str, Any]:
        year = dt.date.today().year
        profiles = composed(self)._profiles_by_raw_author()
        start_date = dt.date(year - 1, 12, 1).isoformat()
        end_date = dt.date(year, 12, 31).isoformat()
        docs = list(self.db.daily_author_activity.find(_date_query(start_date, end_date), {"_id": 0}))
        authors = set(composed(self).list_authors())
        docs_by_author: dict[str, list[dict[str, Any]]] = {}

        for item in docs:
            raw_author = str(item.get("author") or "")

            if raw_author:
                authors.add(raw_author)
                docs_by_author.setdefault(raw_author, []).append(item)

        author_summaries = []

        for raw_author in sorted(authors):
            profile = profiles.get(raw_author, {})
            author_docs = docs_by_author.get(raw_author, [])
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

