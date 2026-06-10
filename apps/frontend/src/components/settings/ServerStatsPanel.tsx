import { useEffect, useState } from "react";
import { apiFetch } from "../../api/client";
import type { ServerStats } from "../../types/dashboard";
import { readStorageItem, sessionBrowserStorage, writeStorageCache } from "../../utils/browserStorage";
import { DiskUsageCard } from "./DiskUsageCard";
import { ServicesStatusCard } from "./ServicesStatusCard";

const SERVER_STATS_CACHE_KEY = "al.serverStats.cache";
let cachedServerStats: ServerStats | null = readCachedServerStats();
type ServerStatsRefreshMode = "disk" | "services";

export function ServerStatsPanel() {
  const [stats, setStats] = useState<ServerStats | null>(() => cachedServerStats);
  const [loading, setLoading] = useState(() => cachedServerStats === null);
  const [diskRefreshing, setDiskRefreshing] = useState(false);
  const [servicesRefreshing, setServicesRefreshing] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  const [error, setError] = useState("");
  const [rebootMessage, setRebootMessage] = useState("");

  async function loadStats(refreshMode?: ServerStatsRefreshMode) {
    if (refreshMode === "disk") {
      setDiskRefreshing(true);
    } else if (refreshMode === "services") {
      setServicesRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const params = refreshMode ? `?${new URLSearchParams({ refresh: refreshMode }).toString()}` : "";
      const response = await apiFetch(`/api/v1/settings/server-stats${params}`);

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
      if (refreshMode === "disk") {
        setDiskRefreshing(false);
      } else if (refreshMode === "services") {
        setServicesRefreshing(false);
      }
    }
  }

  async function requestReboot(): Promise<boolean> {
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
      return true;
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "Server reboot request failed";
      setRebootMessage(`Could not request server reboot: ${message}`);
      return false;
    } finally {
      setRebooting(false);
    }
  }

  useEffect(() => {
    void loadStats();
  }, []);

  const isDiskRefreshing = diskRefreshing || stats?.refreshing === true;
  const isServicesRefreshing = servicesRefreshing;

  return (
    <div className="server-stats-card-row">
      <DiskUsageCard
        stats={stats}
        loading={loading && !stats}
        refreshing={isDiskRefreshing}
        error={error}
        onRefresh={() => void loadStats("disk")}
      />
      <ServicesStatusCard
        services={stats?.services ?? []}
        loading={loading && !stats}
        ready={stats?.ready}
        refreshing={isServicesRefreshing}
        rebooting={rebooting}
        rebootMessage={rebootMessage}
        onRefresh={() => void loadStats("services")}
        onReboot={requestReboot}
      />
    </div>
  );
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
