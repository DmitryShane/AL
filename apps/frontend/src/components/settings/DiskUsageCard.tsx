import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { apiFetch } from "../../api/client";
import type { ServerStats } from "../../types/dashboard";
import { readStorageItem, sessionBrowserStorage, writeStorageCache } from "../../utils/browserStorage";
import { formatDateTime } from "./serverStatsFormatters";

const DISK_USAGE_CACHE_KEY = "al.serverStats.cache";
let cachedDiskStats: ServerStats | null = readCachedDiskStats();

type ReadyServerStats = ServerStats & { root: NonNullable<ServerStats["root"]> };

export function DiskUsageCard() {
  const [stats, setStats] = useState<ServerStats | null>(() => cachedDiskStats);
  const [loading, setLoading] = useState(() => cachedDiskStats === null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function loadDiskUsage(refresh = false) {
    if (refresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const params = refresh ? `?${new URLSearchParams({ refresh: "disk" }).toString()}` : "";
      const response = await apiFetch(`/api/v1/settings/server-stats${params}`);

      if (!response.ok) {
        throw new Error("Server stats request failed");
      }

      const payload = (await response.json()) as ServerStats;
      if (payload.ready === false && cachedDiskStats) {
        setStats({ ...cachedDiskStats, ...diskStatusFields(payload) });
      } else {
        setStats(payload);
      }
      if (payload.ready !== false && payload.root) {
        cachedDiskStats = payload;
        writeCachedDiskStats(payload);
      }
      setError("");
    } catch {
      setError("Could not load server statistics.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void loadDiskUsage();
  }, []);

  const categories = useMemo(() => {
    if (!stats) {
      return [];
    }

    return stats.categories.filter((category) => category.key !== "var").sort((left, right) => right.bytes - left.bytes);
  }, [stats]);

  const displayStats: ReadyServerStats | null = stats?.ready === false || !stats?.root ? null : stats as ReadyServerStats;
  const usedPercent = Math.max(0, Math.min(100, displayStats?.root?.usedPercent ?? 0));
  const accentColor = serverStatsAccentColor(usedPercent);
  const panelStyle = {
    "--server-stats-accent": accentColor
  } as CSSProperties;
  const donutStyle = {
    background: `conic-gradient(var(--server-stats-accent) ${usedPercent * 3.6}deg, rgba(148, 163, 184, 0.18) 0deg)`
  };

  return (
    <section className={`panel server-stats-panel server-stats-panel-${displayStats?.root?.warningLevel ?? "ok"}`} data-doc-target="settings-disk-usage" style={panelStyle}>
      <div className="server-stats-header">
        <div>
          <h2>Disk Usage</h2>
          <p className="settings-caption">Filesystem capacity and application storage.</p>
        </div>
        <button className="server-stats-refresh-button" onClick={() => void loadDiskUsage(true)} disabled={refreshing}>
          {refreshing ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {loading && !stats ? <p className="notice">Loading disk usage...</p> : null}
      {stats?.ready === false ? <p className="notice">Preparing disk usage...</p> : null}
      {error ? <p className="notice error">{error}</p> : null}

      {displayStats ? (
        <>
          <div className="server-stats-disk-column">
            <div className="server-stats-summary">
              <div className="server-stats-donut" style={donutStyle}>
                <div>
                  <strong>{formatPercent(usedPercent)}</strong>
                  <span>used</span>
                </div>
              </div>
              <div className="server-stats-metrics">
                <Metric label="Used" value={formatBytes(displayStats.root.usedBytes)} />
                <Metric label="Free" value={formatBytes(displayStats.root.freeBytes)} />
                <Metric label="Total" value={formatBytes(displayStats.root.totalBytes)} />
                <Metric label="Host" value={displayStats.hostname || "server"} />
              </div>
            </div>

            <div className="server-stats-categories">
              {categories.map((category) => {
                const categoryPercent = displayStats.root.usedBytes > 0 ? Math.min(100, (category.bytes / displayStats.root.usedBytes) * 100) : 0;

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
          </div>

          <p className="server-stats-footnote">
            Last updated {formatDateTime(displayStats.generatedAt)}.
            {displayStats.nextScheduledRefreshAt ? ` Next automatic refresh ${formatDateTime(displayStats.nextScheduledRefreshAt)}.` : ""}
            {refreshing ? " Disk refresh in progress." : ""}
            {displayStats.lastRefreshError ? ` Last refresh failed: ${displayStats.lastRefreshError}` : ""}
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

function serverStatsAccentColor(usedPercent: number): string {
  if (usedPercent >= 95) {
    return "#dc2626";
  }

  if (usedPercent >= 85) {
    return "#ef4444";
  }

  if (usedPercent >= 70) {
    return "#f59e0b";
  }

  return "#35a86b";
}

function readCachedDiskStats(): ServerStats | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const value = readStorageItem(sessionBrowserStorage(), DISK_USAGE_CACHE_KEY);

    if (!value) {
      return null;
    }

    return JSON.parse(value) as ServerStats;
  } catch {
    return null;
  }
}

function writeCachedDiskStats(stats: ServerStats): void {
  try {
    writeStorageCache(sessionBrowserStorage(), DISK_USAGE_CACHE_KEY, JSON.stringify(stats));
  } catch {
    // Storage can be unavailable in private mode; in-memory cache still works.
  }
}

function diskStatusFields(stats: ServerStats): Pick<
  ServerStats,
  "ready" | "cached" | "refreshing" | "refreshStartedAt" | "lastRefreshError" | "nextScheduledRefreshAt" | "cacheExpiresAt"
> {
  return {
    ready: stats.ready,
    cached: stats.cached,
    refreshing: stats.refreshing,
    refreshStartedAt: stats.refreshStartedAt,
    lastRefreshError: stats.lastRefreshError,
    nextScheduledRefreshAt: stats.nextScheduledRefreshAt,
    cacheExpiresAt: stats.cacheExpiresAt
  };
}
