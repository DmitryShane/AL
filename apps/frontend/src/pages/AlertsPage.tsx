import type { AuthorAlert, AuthorRow } from "../types/dashboard";
import { AuthorAvatar } from "../components/AuthorAvatar";
import { alertAuthorCardClassName, alertCardClassName, alertCountBadgeClassName, alertKey, alertSeverityBadgeClassName, authorStatusBadgeClassName, compareAlertAuthors, formatAlertValue, formatAuthorStatus } from "./pageHelpers";
import { AlertSummaryMetric } from "../components/alerts/AlertSummaryMetric";
export function AlertsPage({ authors }: { authors: AuthorRow[] }) {
  const sortedAuthors = [...authors].sort(compareAlertAuthors);
  const totals = sortedAuthors.reduce(
    (acc, author) => {
      const stats = author.alertStats ?? { total: 0, critical: 0, warning: 0 };
      acc.total += stats.total;
      acc.critical += stats.critical;
      acc.warning += stats.warning;

      if (!stats.total) {
        acc.healthy += 1;
      }

      return acc;
    },
    { total: 0, critical: 0, warning: 0, healthy: 0 }
  );

  const alertTypeBreakdownMap = new Map<
    string,
    { count: number; title: string; severity: AuthorAlert["severity"] }
  >();

  for (const author of sortedAuthors) {
    for (const alert of author.alerts ?? []) {
      const key = `${alert.type}\0${alert.severity}`;
      const prev = alertTypeBreakdownMap.get(key);

      if (prev) {
        prev.count += 1;
      }
      else {
        alertTypeBreakdownMap.set(key, { count: 1, title: alert.title, severity: alert.severity });
      }
    }
  }

  const alertTypeBreakdown = [...alertTypeBreakdownMap.entries()]
    .map(([breakdownKey, row]) => ({ breakdownKey, ...row }))
    .sort((left, right) => {
      if (right.count !== left.count) {
        return right.count - left.count;
      }

      return left.title.localeCompare(right.title);
    });

  return (
    <section className="page-section alerts-page">
      <div className="alerts-summary-strip">
        <AlertSummaryMetric label="Total alerts" value={totals.total} tone={totals.total ? "warning" : "healthy"} />
        <AlertSummaryMetric label="Critical" value={totals.critical} tone={totals.critical ? "critical" : "neutral"} />
        <AlertSummaryMetric label="Warning" value={totals.warning} tone={totals.warning ? "warning" : "neutral"} />
        <AlertSummaryMetric label="Healthy authors" value={totals.healthy} tone="healthy" />
      </div>

      {alertTypeBreakdown.length ? (
        <div className="alerts-type-breakdown" aria-label="Alert types breakdown">
          <span className="alerts-type-breakdown-label">By alert type</span>
          <ul className="alerts-type-breakdown-list">
            {alertTypeBreakdown.map((row) => (
              <li className={`alerts-type-breakdown-item ${row.severity}`} key={row.breakdownKey}>
                <span className="alerts-type-breakdown-title">{row.title}</span>
                <span className="alerts-type-breakdown-count">×{row.count}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="alerts-card-grid">
        {sortedAuthors.map((author) => {
          const alerts = author.alerts ?? [];
          const stats = author.alertStats ?? { total: 0, critical: 0, warning: 0 };

          return (
            <article className={alertAuthorCardClassName(stats)} key={author.rawAuthor}>
              <div className="alert-author-header">
                <AuthorAvatar displayName={author.displayName} authorColor={author.authorColor} avatarUrl={author.avatarUrl} />
                <div>
                  <strong>{author.displayName}</strong>
                  <small>{author.authorEmail || author.rawAuthor}</small>
                  <small>{author.team || "No team"}</small>
                </div>
                <span className={authorStatusBadgeClassName(author.status, author.stalePresence)}>{formatAuthorStatus(author)}</span>
              </div>

              <div className="alert-count-stack">
                <span className={alertCountBadgeClassName(stats.total ? "total" : "healthy")}>{stats.total ? `${stats.total} total` : "Healthy"}</span>
                <span className={alertCountBadgeClassName(stats.critical ? "critical" : "muted")}>{stats.critical} critical</span>
                <span className={alertCountBadgeClassName(stats.warning ? "warning" : "muted")}>{stats.warning} warning</span>
              </div>

              <div className="alert-stack">
                {alerts.length ? (
                  alerts.map((alert, index) => (
                    <div className={alertCardClassName(alert.severity)} key={alertKey(alert, author.rawAuthor, index)}>
                      <div>
                        <strong>{alert.title}</strong>
                        <span className={alertSeverityBadgeClassName(alert.severity)}>{alert.severity}</span>
                      </div>
                      <p>{alert.message}</p>
                      <small>{formatAlertValue(alert)}</small>
                    </div>
                  ))
                ) : (
                  <p className="empty">No alerts.</p>
                )}
              </div>
            </article>
          );
        })}
      </div>
      {!sortedAuthors.length ? <p className="empty">No authors for the selected period.</p> : null}
    </section>
  );
}
