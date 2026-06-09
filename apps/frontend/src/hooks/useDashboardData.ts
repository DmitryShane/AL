import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { REFRESH_INTERVAL_MS } from "../constants/dashboard";
import {
  loadCachedDashboardSummary,
  pageUsesDashboardSummary,
  readCachedAuthors,
  readCachedActivitySummary,
  readCachedSettingsSummary,
  saveCachedActivitySummary,
  saveCachedAuthors,
  saveCachedDashboardSummary,
  saveCachedSettingsSummary,
  saveDateRange,
  summaryViewForPage
} from "../utils/dashboardStorage";
import type { ActivitySummary, AuthorRow, DateRange, Health, Page, SiteUser, Summary } from "../types/dashboard";

export type HealthStatus = "checking" | "online" | "offline";

export function useDashboardData({
  page,
  dateRange,
  authUser,
  authLoading,
  clearAuthState
}: {
  page: Page | null;
  dateRange: DateRange;
  authUser: SiteUser | null;
  authLoading: boolean;
  clearAuthState: () => void;
}) {
  const [appliedDateRange, setAppliedDateRange] = useState<DateRange>(() => dateRange);
  const [summary, setSummary] = useState<Summary | null>(() => page ? loadCachedDashboardSummary(page, dateRange) : null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthStatus, setHealthStatus] = useState<HealthStatus>("checking");
  const [cachedAuthors, setCachedAuthors] = useState<AuthorRow[]>(() => readCachedAuthors(dateRange));
  const [cachedActivitySummary, setCachedActivitySummary] = useState<ActivitySummary | null>(() => readCachedActivitySummary(dateRange));
  const [cachedSettingsSummary, setCachedSettingsSummary] = useState<Summary | null>(() => readCachedSettingsSummary());
  const [loading, setLoading] = useState(true);
  const [refreshingReports, setRefreshingReports] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(showLoading = true) {
    if (!authUser) {
      if (!authLoading) {
        setLoading(false);
      }
      return;
    }

    if (!page) {
      setSummary(null);
      setLoading(false);
      return;
    }

    if (showLoading) {
      setLoading(true);
    }

    setError(null);

    const requestedDateRange = dateRange;
    const requestedPage = page;
    const cachedSummary = loadCachedDashboardSummary(requestedPage, requestedDateRange);

    if (showLoading && cachedSummary) {
      setSummary(cachedSummary);
      setAppliedDateRange(requestedDateRange);
    }

    try {
      if (requestedPage === "alerts") {
        const [healthResponse, meResponse] = await Promise.all([apiFetch(`/api/v1/health`), apiFetch(`/api/v1/auth/me`)]);

        if (meResponse.status === 401) {
          clearAuthState();
          return;
        }

        if (!healthResponse.ok || !meResponse.ok) {
          setHealthStatus(healthResponse.ok ? "online" : "offline");
          throw new Error("Backend request failed");
        }

        setHealth(await healthResponse.json());
        setHealthStatus("online");
        return;
      }

      if (requestedPage === "settings") {
        if (showLoading && cachedSettingsSummary) {
          setCachedSettingsSummary(cachedSettingsSummary);
        }

        void apiFetch(`/api/v1/health`)
          .then(async (healthResponse) => {
            if (!healthResponse.ok) {
              setHealthStatus("offline");
              return;
            }

            setHealth(await healthResponse.json());
            setHealthStatus("online");
          })
          .catch(() => {
            setHealthStatus("offline");
          });

        const bootstrapResponse = await apiFetch(`/api/v1/settings/bootstrap`);

        if (bootstrapResponse.status === 401) {
          clearAuthState();
          return;
        }

        if (!bootstrapResponse.ok) {
          throw new Error("Backend request failed");
        }

        const nextSummary = await bootstrapResponse.json() as Summary;
        setSummary(nextSummary);
        setCachedSettingsSummary(nextSummary);
        saveCachedSettingsSummary(nextSummary);
        setAppliedDateRange(requestedDateRange);
        return;
      }

      const params = new URLSearchParams({
        startDate: requestedDateRange.startDate,
        endDate: requestedDateRange.endDate,
        view: summaryViewForPage(requestedPage)
      });

      if (requestedDateRange.preset === "live") {
        params.set("dateMode", "authorLocalToday");
      }

      const [healthResponse, summaryResponse] = await Promise.all([
        apiFetch(`/api/v1/health`),
        apiFetch(`/api/v1/reports/summary?${params.toString()}`)
      ]);

      if (summaryResponse.status === 401) {
        clearAuthState();
        return;
      }

      if (!healthResponse.ok || !summaryResponse.ok) {
        setHealthStatus(healthResponse.ok ? "online" : "offline");
        throw new Error("Backend request failed");
      }

      setHealth(await healthResponse.json());
      setHealthStatus("online");
      const nextSummary = await summaryResponse.json() as Summary;
      const nextAuthors = nextSummary.activitySummary.authors;
      setSummary(nextSummary);
      const snapshotPreparing = nextSummary.activitySummary.snapshot?.status === "preparing";
      if (!snapshotPreparing) {
        saveCachedDashboardSummary(requestedPage, requestedDateRange, nextSummary);
      }
      setCachedAuthors(nextAuthors);
      saveCachedAuthors(requestedDateRange, nextAuthors);

      if (requestedPage === "activity") {
        setCachedActivitySummary(nextSummary.activitySummary);
        if (!snapshotPreparing) {
          saveCachedActivitySummary(requestedDateRange, nextSummary.activitySummary);
        }
      }

      setAppliedDateRange(requestedDateRange);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [authLoading, authUser?.email, dateRange.startDate, dateRange.endDate, dateRange.preset, page]);

  useEffect(() => {
    saveDateRange(dateRange);
  }, [dateRange]);

  useEffect(() => {
    setCachedAuthors(readCachedAuthors(dateRange));
    setCachedActivitySummary(readCachedActivitySummary(dateRange));
  }, [dateRange.startDate, dateRange.endDate, dateRange.preset]);

  const dashboardRefreshMs = dashboardRefreshIntervalMs(summary);

  useEffect(() => {
    if (!authUser) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void load(false);
    }, dashboardRefreshMs);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [authUser?.email, dateRange.startDate, dateRange.endDate, dateRange.preset, dashboardRefreshMs, page]);

  useEffect(() => {
    if (!authUser || page !== "activity" || summary?.activitySummary.snapshot?.status !== "preparing") {
      return;
    }

    const intervalId = window.setInterval(() => {
      void load(false);
    }, 3000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [authUser?.email, page, summary?.activitySummary.snapshot?.status, dateRange.startDate, dateRange.endDate, dateRange.preset]);

  async function requestReportRefresh(author?: string | null) {
    setRefreshingReports(true);
    setError(null);

    try {
      const response = await apiFetch(`/api/v1/reports/request-refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ author: author ?? null })
      });

      if (!response.ok) {
        throw new Error("Report refresh request failed");
      }

      await load(false);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unknown error");
    } finally {
      setRefreshingReports(false);
    }
  }

  function showCachedSummaryForPage(nextPage: Page) {
    if (nextPage === "settings") {
      const nextSettingsSummary = readCachedSettingsSummary();
      setCachedSettingsSummary(nextSettingsSummary);
      setSummary(nextSettingsSummary);
    } else if (pageUsesDashboardSummary(nextPage)) {
      setSummary(loadCachedDashboardSummary(nextPage, dateRange));
    } else {
      setSummary(null);
    }
  }

  function clearDashboardState() {
    setSummary(null);
    setHealth(null);
  }

  return {
    appliedDateRange,
    summary,
    health,
    healthStatus,
    cachedAuthors,
    cachedActivitySummary,
    cachedSettingsSummary,
    loading,
    refreshingReports,
    error,
    load,
    requestReportRefresh,
    showCachedSummaryForPage,
    clearDashboardState
  };
}

function dashboardRefreshIntervalMs(summary: Summary | null) {
  const seconds = summary?.intervalSettings.defaultSendIntervalSeconds ?? REFRESH_INTERVAL_MS / 1000;
  return Math.max(1000, seconds * 1000);
}
