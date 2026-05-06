import type { OpenAIStats } from "../../../../types/dashboard";
import { formatTimestamp } from "../../../../pages/pageHelpers";

type OpenAIStatsCardProps = {
  openAIStats: OpenAIStats | null;
  openAIStatsError: string;
  openAIStatsLoading: boolean;
  onRefresh: () => void;
};

export function OpenAIStatsCard({ openAIStats, openAIStatsError, openAIStatsLoading, onRefresh }: OpenAIStatsCardProps) {
  return (
    <div className="panel meeting-summary-openai-panel">
      <div className="meeting-summary-panel-header">
        <div className="openai-stats-title">
          <h3>OpenAI Stats</h3>
          <a href="https://platform.openai.com/" target="_blank" rel="noreferrer">
            OpenAI Platform
          </a>
        </div>
        <button className="primary-outline-button" onClick={onRefresh} disabled={openAIStatsLoading}>
          {openAIStatsLoading ? "Loading..." : "Refresh"}
        </button>
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
            Current month
            {openAIStats.projectId ? ", organization Mempic, project ALManager" : ""}
            {openAIStats.cached ? ", cached" : ""}
            {openAIStats.generatedAt ? `, updated ${formatTimestamp(openAIStats.generatedAt)}` : ""}
          </p>
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
