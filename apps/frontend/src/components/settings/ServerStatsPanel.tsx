import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../api/client";
import type { ServerStats } from "../../types/dashboard";

const REFRESH_INTERVAL_MS = 60_000;

export function ServerStatsPanel() {
  const [stats, setStats] = useState<ServerStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function loadStats(isRefresh = false) {
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const response = await apiFetch("/api/v1/settings/server-stats");

      if (!response.ok) {
        throw new Error("Server stats request failed");
      }

      const payload = (await response.json()) as ServerStats;
      setStats(payload);
      setError("");
    } catch {
      setError("Could not load server statistics.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void loadStats();
    const intervalId = window.setInterval(() => void loadStats(true), REFRESH_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, []);

  const categories = useMemo(() => {
    if (!stats) {
      return [];
    }

    return stats.categories.filter((category) => category.key !== "var").sort((left, right) => right.bytes - left.bytes);
  }, [stats]);

  const usedPercent = Math.max(0, Math.min(100, stats?.root.usedPercent ?? 0));
  const donutStyle = {
    background: `conic-gradient(var(--server-stats-accent) ${usedPercent * 3.6}deg, rgba(148, 163, 184, 0.18) 0deg)`
  };

  return (
    <section className={`panel server-stats-panel server-stats-panel-${stats?.root.warningLevel ?? "ok"}`}>
      <div className="server-stats-header">
        <div>
          <h2>Server Stats</h2>
          <p className="settings-caption">Read-only production disk usage overview.</p>
        </div>
        <button className="ghost-button" onClick={() => void loadStats(true)} disabled={refreshing}>
          {refreshing ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {loading && !stats ? <p className="notice">Loading server statistics...</p> : null}
      {error ? <p className="notice error">{error}</p> : null}

      {stats ? (
        <>
          <div className="server-stats-summary">
            <div className="server-stats-donut" style={donutStyle}>
              <div>
                <strong>{formatPercent(usedPercent)}</strong>
                <span>used</span>
              </div>
            </div>
            <div className="server-stats-metrics">
              <Metric label="Used" value={formatBytes(stats.root.usedBytes)} />
              <Metric label="Free" value={formatBytes(stats.root.freeBytes)} />
              <Metric label="Total" value={formatBytes(stats.root.totalBytes)} />
              <Metric label="Host" value={stats.hostname || "server"} />
            </div>
          </div>

          <div className="server-stats-categories">
            {categories.map((category) => {
              const categoryPercent = stats.root.usedBytes > 0 ? Math.min(100, (category.bytes / stats.root.usedBytes) * 100) : 0;

              return (
                <div className="server-stats-category" key={category.key}>
                  <div className="server-stats-category-label">
                    <span>{category.label}</span>
                    <strong>{category.exists ? formatBytes(category.bytes) : "Not found"}</strong>
                  </div>
                  <div className="server-stats-bar" title={category.path}>
                    <span style={{ width: `${category.exists ? categoryPercent : 0}%` }} />
                  </div>
                </div>
              );
            })}
          </div>

          <p className="server-stats-footnote">
            Last updated {formatDateTime(stats.generatedAt)}. Auto-refreshes every 60 seconds while this tab is open.
          </p>
        </>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="server-stats-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }

  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
}

function formatPercent(value: number): string {
  return `${Math.round(value * 10) / 10}%`;
}

function formatDateTime(value: string): string {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "just now";
  }

  return date.toLocaleString();
}
