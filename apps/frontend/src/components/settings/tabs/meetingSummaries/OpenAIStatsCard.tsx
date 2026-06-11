import type { OpenAIStats } from "../../../../types/dashboard";
import { formatTimestamp } from "../../../../pages/pageHelpers";

type OpenAIStatsCardProps = {
  openAIStats: OpenAIStats | null;
  openAIStatsError: string;
  openAIStatsLoading: boolean;
  openAIStatsRefreshMode: "month" | "totals" | null;
  onRefresh: () => void;
  onRefreshTotals: () => void;
};

export function OpenAIStatsCard({
  openAIStats,
  openAIStatsError,
  openAIStatsLoading,
  openAIStatsRefreshMode,
  onRefresh,
  onRefreshTotals
}: OpenAIStatsCardProps) {
  const controlsDisabled = openAIStatsLoading || openAIStatsRefreshMode !== null;
  const syncProgressTotal = openAIStats?.syncProgressTotal ?? 0;
  const syncProgressCurrent = openAIStats?.syncProgressCurrent ?? 0;
  const syncProgressPercent = syncProgressTotal > 0 ? Math.min(100, Math.round((syncProgressCurrent / syncProgressTotal) * 100)) : 0;
  const isSyncing = openAIStats?.syncStatus === "syncingTotals" || openAIStats?.syncStatus === "syncingMonth";

  return (
    <div className="panel meeting-summary-openai-panel" data-doc-target="settings-openai-stats">
      <div className="meeting-summary-panel-header">
        <div className="openai-stats-title">
          <h3>OpenAI Stats</h3>
          <a href="https://platform.openai.com/" target="_blank" rel="noreferrer">
            OpenAI Platform
          </a>
        </div>
        <div className="openai-stats-actions">
          <button className="primary-outline-button" onClick={onRefresh} disabled={controlsDisabled}>
            {openAIStatsRefreshMode === "month" ? "Loading..." : "Refresh"}
          </button>
          <button className="primary-outline-button" onClick={onRefreshTotals} disabled={controlsDisabled}>
            {openAIStatsRefreshMode === "totals" ? "Loading..." : "Refresh totals"}
          </button>
        </div>
      </div>
      {openAIStatsError ? (
        <p className="empty">{openAIStatsError}</p>
      ) : openAIStats ? (
        <div className="openai-stats-grid">
          <div>
            <span>Total spend</span>
            <strong>{formatOpenAICurrency(openAIStats.totalSpend ?? 0, openAIStats.currency)}</strong>
          </div>
          <div>
            <span>Month spend{openAIStats.periodStart ? ` (${formatOpenAIMonth(openAIStats.periodStart)})` : ""}</span>
            <strong>{formatOpenAICurrency(openAIStats.monthSpend ?? 0, openAIStats.currency)}</strong>
          </div>
          <div>
            <span>Total tokens</span>
            <strong>{formatCompactNumber(openAIStats.totalTokens ?? 0)}</strong>
          </div>
          <div>
            <span>Total requests</span>
            <strong>{formatCompactNumber(openAIStats.totalRequests ?? 0)}</strong>
          </div>
          <p>
            Organization totals, current month spend
            {openAIStats.syncStatus === "syncingMonth" ? ", syncing current month" : ""}
            {openAIStats.syncStatus === "syncingTotals" ? ", syncing totals" : ""}
            {openAIStats.cached ? ", cached" : ""}
            {openAIStats.lastRefreshedAt ? `, updated ${formatTimestamp(openAIStats.lastRefreshedAt)}` : ""}
          </p>
          {isSyncing ? (
            <div className="openai-stats-progress">
              <div className="openai-stats-progress-label">
                <span>{openAIStats.syncProgressLabel || (openAIStats.syncStatus === "syncingMonth" ? "Syncing current month" : "Syncing totals")}</span>
                {syncProgressTotal > 0 ? <strong>{syncProgressPercent}%</strong> : null}
              </div>
              <div className="openai-stats-progress-track">
                <div style={{ width: `${syncProgressPercent}%` }} />
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="empty">OpenAI stats have not loaded yet.</p>
      )}
    </div>
  );
}

function formatCompactNumber(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

function formatOpenAICurrency(value: number, currency = "USD") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: value > 0 && value < 1 ? 4 : 2
  }).format(value);
}

function formatOpenAIMonth(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    year: "numeric"
  }).format(new Date(value));
}
