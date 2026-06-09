import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { apiFetch } from "../../api/client";
import type { ServerStats, ServerStatsService } from "../../types/dashboard";
import { readStorageItem, sessionBrowserStorage, writeStorageCache } from "../../utils/browserStorage";
import { Modal } from "../ui/Modal";

const SERVER_STATS_CACHE_KEY = "al.serverStats.cache";
let cachedServerStats: ServerStats | null = readCachedServerStats();
type ReadyServerStats = ServerStats & { root: NonNullable<ServerStats["root"]> };

export function ServerStatsPanel() {
  const [stats, setStats] = useState<ServerStats | null>(() => cachedServerStats);
  const [loading, setLoading] = useState(() => cachedServerStats === null);
  const [refreshing, setRefreshing] = useState(false);
  const [rebootModalOpen, setRebootModalOpen] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  const [error, setError] = useState("");
  const [rebootMessage, setRebootMessage] = useState("");

  async function loadStats(isRefresh = false, forceRefresh = false) {
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const response = await apiFetch(`/api/v1/settings/server-stats${forceRefresh ? "?refresh=true" : ""}`);

      if (!response.ok) {
        throw new Error("Server stats request failed");
      }

      const payload = (await response.json()) as ServerStats;
      if (payload.ready === false && cachedServerStats) {
        setStats({ ...cachedServerStats, ...serverStatsStatusFields(payload) });
      } else {
        setStats(payload);
      }
      if (payload.ready !== false && payload.root) {
        cachedServerStats = payload;
        writeCachedServerStats(payload);
      }
      setError("");
    } catch {
      setError("Could not load server statistics.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function requestReboot() {
    setRebooting(true);
    setRebootMessage("");

    try {
      const response = await apiFetch("/api/v1/settings/server-reboot", { method: "POST" });

      if (!response.ok) {
        throw new Error("Server reboot request failed");
      }

      const payload = (await response.json()) as { ok?: boolean; error?: string };

      if (!payload.ok) {
        throw new Error(payload.error || "Server reboot request failed");
      }

      setRebootMessage("Server services restart requested. The dashboard may disconnect for a few moments.");
      setRebootModalOpen(false);
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "Server reboot request failed";
      setRebootMessage(`Could not request server reboot: ${message}`);
    } finally {
      setRebooting(false);
    }
  }

  useEffect(() => {
    void loadStats(cachedServerStats !== null);
  }, []);

  const categories = useMemo(() => {
    if (!stats) {
      return [];
    }

    return stats.categories.filter((category) => category.key !== "var").sort((left, right) => right.bytes - left.bytes);
  }, [stats]);

  const displayStats: ReadyServerStats | null = stats?.ready === false || !stats?.root ? null : stats as ReadyServerStats;
  const isRefreshing = refreshing || stats?.refreshing === true;
  const usedPercent = Math.max(0, Math.min(100, displayStats?.root?.usedPercent ?? 0));
  const accentColor = serverStatsAccentColor(usedPercent);
  const panelStyle = {
    "--server-stats-accent": accentColor
  } as CSSProperties;
  const donutStyle = {
    background: `conic-gradient(var(--server-stats-accent) ${usedPercent * 3.6}deg, rgba(148, 163, 184, 0.18) 0deg)`
  };

  return (
    <section className={`panel server-stats-panel server-stats-panel-${displayStats?.root?.warningLevel ?? "ok"}`} style={panelStyle}>
      <div className="server-stats-header">
        <div>
          <h2>Server Stats</h2>
          <p className="settings-caption">Read-only production disk usage overview.</p>
        </div>
        <div className="server-stats-actions">
          <button className="server-stats-refresh-button" onClick={() => void loadStats(true, true)} disabled={refreshing || rebooting}>
            {isRefreshing ? "Refreshing..." : "Refresh"}
          </button>
          <button className="server-stats-reboot-button" onClick={() => setRebootModalOpen(true)} disabled={rebooting}>
            {rebooting ? "Rebooting..." : "Reboot"}
          </button>
        </div>
      </div>

      {loading && !stats ? <p className="notice">Loading server statistics...</p> : null}
      {stats?.ready === false ? <p className="notice">Preparing server statistics...</p> : null}
      {error ? <p className="notice error">{error}</p> : null}
      {rebootMessage ? <p className="notice">{rebootMessage}</p> : null}

      {displayStats ? (
        <>
          <div className="server-stats-grid">
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

            <div className="server-stats-services-column">
              <div>
                <h3>Services</h3>
                <p className="settings-caption">Runtime status of server processes.</p>
              </div>
              <div className="server-stats-services">
                {(displayStats.services ?? []).map((service) => (
                  <ServiceStatus key={service.key} service={service} />
                ))}
              </div>
            </div>
          </div>

          <p className="server-stats-footnote">
            Last updated {formatDateTime(displayStats.generatedAt)}.
            {displayStats.nextScheduledRefreshAt ? ` Next automatic refresh ${formatDateTime(displayStats.nextScheduledRefreshAt)}.` : ""}
            {isRefreshing ? " Refresh in progress." : ""}
            {displayStats.lastRefreshError ? ` Last refresh failed: ${displayStats.lastRefreshError}` : ""}
          </p>
        </>
      ) : null}

      {rebootModalOpen ? (
        <ServerRebootConfirmModal
          saving={rebooting}
          onCancel={() => setRebootModalOpen(false)}
          onConfirm={() => void requestReboot()}
        />
      ) : null}
    </section>
  );
}

function ServerRebootConfirmModal({
  saving,
  onCancel,
  onConfirm
}: {
  saving: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--scoped-activity-delete"
      ariaLabelledBy="server-reboot-title"
      ariaDescribedBy="server-reboot-desc"
    >
      <div className="scoped-delete-modal__accent" aria-hidden="true" />
      <div className="scoped-delete-modal__body">
        <header className="scoped-delete-modal__header">
          <span className="scoped-delete-modal__badge">Server action</span>
          <h2 id="server-reboot-title">Reboot production server</h2>
          <p id="server-reboot-desc" className="scoped-delete-modal__lead">
            This will reboot the host machine and restart all system services, including the backend, bots, MongoDB, and Nginx.
            The dashboard will be unavailable while the server comes back online.
          </p>
        </header>

        <p className="scoped-delete-modal__description">
          Use this only when you need a full server restart. The request is sent immediately after confirmation.
        </p>

        <div className="modal-actions scoped-delete-modal__actions">
          <button className="server-reboot-cancel-button" type="button" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button className="server-reboot-confirm-button" type="button" onClick={onConfirm} disabled={saving}>
            {saving ? "Requesting..." : "Reboot server"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function ServiceStatus({ service }: { service: ServerStatsService }) {
  return (
    <div className={`server-stats-service server-stats-service-${service.status}`}>
      <div className="server-stats-service-main">
        <span className="server-stats-service-dot" aria-hidden="true" />
        <div>
          <strong>{service.label}</strong>
          <span>{service.unit}</span>
        </div>
      </div>
      <div className="server-stats-service-meta">
        <strong>{formatServiceStatus(service)}</strong>
        <span>{formatServiceDetail(service)}</span>
      </div>
    </div>
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

function formatServiceStatus(service: ServerStatsService): string {
  if (service.status === "running") {
    return "Running";
  }

  if (service.status === "stopped") {
    return "Stopped";
  }

  return "Unknown";
}

function formatServiceDetail(service: ServerStatsService): string {
  const state = service.subState ? `${service.activeState} / ${service.subState}` : service.activeState;

  if (service.status === "running" && service.activeEnteredAt) {
    return `${state}, since ${formatDateTime(service.activeEnteredAt)}`;
  }

  return state;
}

function readCachedServerStats(): ServerStats | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const value = readStorageItem(sessionBrowserStorage(), SERVER_STATS_CACHE_KEY);

    if (!value) {
      return null;
    }

    return JSON.parse(value) as ServerStats;
  } catch {
    return null;
  }
}

function writeCachedServerStats(stats: ServerStats): void {
  try {
    writeStorageCache(sessionBrowserStorage(), SERVER_STATS_CACHE_KEY, JSON.stringify(stats));
  } catch {
    // Storage can be unavailable in private mode; in-memory cache still works.
  }
}

function serverStatsStatusFields(stats: ServerStats): Pick<
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

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "just now";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "just now";
  }

  return date.toLocaleString();
}
