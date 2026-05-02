import { useEffect, useState } from "react";
import { AnalyticsActivityOverview } from "../components/AnalyticsActivityOverview";
import { apiFetch } from "../api/client";
import type { AnalyticsSummary } from "../types/dashboard";
import { avatarStyle, initials } from "./pageHelpers";
export function AnalyticsPage() {
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
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
                <span className="avatar" style={avatarStyle(author.authorColor)}>{initials(author.displayName)}</span>
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
              avatar={<span className="avatar" style={avatarStyle(selected.authorColor)}>{initials(selected.displayName)}</span>}
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

