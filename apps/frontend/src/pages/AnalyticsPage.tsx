import { useEffect, useState } from "react";
import { AnalyticsActivityOverview } from "../components/AnalyticsActivityOverview";
import { AuthorAvatar } from "../components/AuthorAvatar";
import { apiFetch } from "../api/client";
import { ANALYTICS_SUMMARY_CACHE_KEY } from "../constants/dashboard";
import type { AnalyticsSummary } from "../types/dashboard";

export function AnalyticsPage() {
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(() => loadCachedAnalyticsSummary());
  const [selectedAuthor, setSelectedAuthor] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadAnalytics(showLoading = true) {
    if (showLoading) {
      setLoading(true);
    }

    try {
      const response = await apiFetch(`/api/v1/analytics/summary`);

      if (!response.ok) {
        throw new Error("Analytics request failed");
      }

      const data: AnalyticsSummary = await response.json();
      setAnalytics(data);
      saveCachedAnalyticsSummary(data);
      setSelectedAuthor((current) => current || (data.authors[0]?.rawAuthor ?? ""));
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load analytics.");
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    void loadAnalytics();
    const intervalId = window.setInterval(() => void loadAnalytics(false), 5 * 60 * 1000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, []);

  const selected = analytics?.authors.find((author) => author.rawAuthor === selectedAuthor) ?? analytics?.authors[0] ?? null;

  return (
    <section className="page-section analytics-page">
      {error ? <p className="notice error">{error}</p> : null}

      {analytics ? (
        <>
          <div className="author-card-strip analytics-author-strip">
            {analytics.authors.map((author) => (
              <button
                className={selectedAuthor === author.rawAuthor ? "author-card active" : "author-card"}
                key={author.rawAuthor}
                onClick={() => setSelectedAuthor(author.rawAuthor)}
              >
                <AuthorAvatar displayName={author.displayName} authorColor={author.authorColor} avatarUrl={author.avatarUrl} />
                <strong>{author.displayName}</strong>
                <small>{author.team || "No team"}</small>
                <div className="mini-metrics">
                  <span>{analytics.year}</span>
                  <span>{author.months.length} months</span>
                </div>
              </button>
            ))}
          </div>

          {selected ? (
            <AnalyticsActivityOverview
              author={selected}
              year={analytics.year}
              avatar={
                <AuthorAvatar displayName={selected.displayName} authorColor={selected.authorColor} avatarUrl={selected.avatarUrl} />
              }
            />
          ) : (
            <p className="empty">No analytics authors yet.</p>
          )}
        </>
      ) : loading ? (
        <p className="notice">Loading analytics...</p>
      ) : (
        <p className="empty">No analytics data yet.</p>
      )}
    </section>
  );
}

function loadCachedAnalyticsSummary() {
  try {
    const cached = sessionStorage.getItem(ANALYTICS_SUMMARY_CACHE_KEY);

    if (!cached) {
      return null;
    }

    return JSON.parse(cached) as AnalyticsSummary;
  } catch {
    return null;
  }
}

function saveCachedAnalyticsSummary(summary: AnalyticsSummary) {
  try {
    sessionStorage.setItem(ANALYTICS_SUMMARY_CACHE_KEY, JSON.stringify(summary));
  } catch {
    // Ignore storage failures; live API data is still shown.
  }
}

