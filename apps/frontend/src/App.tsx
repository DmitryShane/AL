import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Activity, BarChart3, Bell, CalendarDays, LogOut, Settings, UsersRound } from "lucide-react";
import { DateRangePicker } from "./components/layout/DateRangePicker";
import { LoginPage } from "./pages/LoginPage";
import { NavButton } from "./components/layout/NavButton";
import { ActivityPage } from "./pages/ActivityPage";
import { AlertsPage } from "./pages/AlertsPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { AuthorsPage } from "./pages/AuthorsPage";
import { CalendarPage } from "./pages/CalendarPage";
import { SettingsPage } from "./pages/SettingsPage";
import { apiFetch, IS_LOCAL_DASHBOARD } from "./api/client";
import {
  ACTIVITY_AUTHOR_STORAGE_KEY,
  AUTH_HINT_STORAGE_KEY,
  DATE_RANGE_STORAGE_KEY,
  DASHBOARD_SUMMARY_CACHE_PREFIX,
  MEETING_AUDIO_RETENTION_OPTIONS,
  MEETING_SUMMARY_LANGUAGES,
  PAGE_SCROLL_STORAGE_PREFIX,
  PAGE_STORAGE_KEY,
  REFRESH_INTERVAL_MS,
  REPORTS_PAGE_STORAGE_KEY,
  SETTINGS_TAB_STORAGE_KEY
} from "./constants/dashboard";
import "./styles.css";

import type { ActivitySummary, AuthorRow, DateRange, Health, Page, SettingsTab, SiteUser, SiteUserRole, Summary } from "./types/dashboard";

const emptyActivitySummary: ActivitySummary = {
  totals: {
    daySeconds: 0,
    telegramDaySeconds: 0,
    pluginDaySeconds: 0,
    rawPluginDaySeconds: 0,
    telegramToFirstActivitySeconds: 0,
    activeSeconds: 0,
    idleSeconds: 0,
    meetingSeconds: 0,
    breakSeconds: 0,
    overtimeActiveSeconds: 0
  },
  authors: [],
  profiles: [],
  authorAliases: [],
  activityMix: [],
  savedPrefabs: [],
  overtimeActivityMix: [],
  overtimeSavedPrefabs: [],
  hourlyActivityByAuthor: []
};

function App() {
  const [page, setPage] = useState<Page>(() => loadSavedPage());
  const [isRestoringScroll, setIsRestoringScroll] = useState(() => getSavedPageScroll(loadSavedPage()) > 0);
  const isRestoringScrollRef = useRef(isRestoringScroll);
  const skipNextScrollRestoreRef = useRef(false);
  const [authUser, setAuthUser] = useState<SiteUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [hasAuthHint, setHasAuthHint] = useState(() => localStorage.getItem(AUTH_HINT_STORAGE_KEY) === "true");
  const [health, setHealth] = useState<Health | null>(null);
  const [dateRange, setDateRange] = useState<DateRange>(() => loadSavedDateRange());
  const [appliedDateRange, setAppliedDateRange] = useState<DateRange>(() => loadSavedDateRange());
  const [summary, setSummary] = useState<Summary | null>(() => loadCachedDashboardSummary(loadSavedPage(), loadSavedDateRange()));
  const [search, setSearch] = useState("");
  const [selectedAuthor, setSelectedAuthorState] = useState<string | null>(() => loadSavedActivityAuthor());
  const [loading, setLoading] = useState(true);
  const [refreshingReports, setRefreshingReports] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    isRestoringScrollRef.current = isRestoringScroll;
  }, [isRestoringScroll]);

  useLayoutEffect(() => {
    const previousScrollRestoration = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";

    return () => {
      window.history.scrollRestoration = previousScrollRestoration;
    };
  }, []);

  useEffect(() => {
    async function loadAuth() {
      try {
        let response = await apiFetch("/api/v1/auth/me");

        if (!response.ok && IS_LOCAL_DASHBOARD) {
          response = await apiFetch("/api/v1/auth/dev-login", { method: "POST" });
        }

        if (response.ok) {
          const payload = await response.json();
          setAuthUser(payload.user);
          setHasAuthHint(true);
          localStorage.setItem(AUTH_HINT_STORAGE_KEY, "true");
        } else {
          setHasAuthHint(false);
          localStorage.removeItem(AUTH_HINT_STORAGE_KEY);
        }
      } catch {
        setAuthUser(null);
        setHasAuthHint(false);
        localStorage.removeItem(AUTH_HINT_STORAGE_KEY);
      } finally {
        setAuthLoading(false);
      }
    }

    void loadAuth();
  }, []);

  async function load(showLoading = true) {
    if (!authUser) {
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
        setAuthUser(null);
        setHasAuthHint(false);
        localStorage.removeItem(AUTH_HINT_STORAGE_KEY);
        return;
      }

      if (!healthResponse.ok || !summaryResponse.ok) {
        throw new Error("Backend request failed");
      }

      setHealth(await healthResponse.json());
      const nextSummary = await summaryResponse.json() as Summary;
      setSummary(nextSummary);
      saveCachedDashboardSummary(requestedPage, requestedDateRange, nextSummary);
      setAppliedDateRange(requestedDateRange);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [authUser?.email, dateRange.startDate, dateRange.endDate, dateRange.preset, page]);

  useEffect(() => {
    localStorage.setItem(DATE_RANGE_STORAGE_KEY, JSON.stringify(dateRange));
  }, [dateRange]);

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

  const canShowCachedDashboard = Boolean(authUser) || (authLoading && hasAuthHint);
  const activitySummary = canShowCachedDashboard ? (summary?.activitySummary ?? emptyActivitySummary) : emptyActivitySummary;
  const authors = useMemo(() => activitySummary.authors.filter((author) => matchesAuthorSearch(author, search)), [activitySummary, search]);
  const activeAuthor = activitySummary.authors.some((author) => author.rawAuthor === selectedAuthor)
    ? selectedAuthor
    : authors[0]?.rawAuthor ?? activitySummary.authors[0]?.rawAuthor ?? null;

  useEffect(() => {
    if (!activeAuthor && activitySummary.authors.length) {
      setSelectedAuthor(activitySummary.authors[0].rawAuthor);
    }
  }, [activeAuthor, activitySummary.authors]);

  useEffect(() => {
    const savePageScroll = () => {
      if (isRestoringScrollRef.current) {
        return;
      }

      sessionStorage.setItem(pageScrollStorageKey(page), String(window.scrollY));
    };

    window.addEventListener("scroll", savePageScroll, { passive: true });
    window.addEventListener("beforeunload", savePageScroll);

    return () => {
      savePageScroll();
      window.removeEventListener("scroll", savePageScroll);
      window.removeEventListener("beforeunload", savePageScroll);
    };
  }, [page]);

  useLayoutEffect(() => {
    if (!canShowCachedDashboard) {
      return;
    }

    if (skipNextScrollRestoreRef.current) {
      skipNextScrollRestoreRef.current = false;
      setIsRestoringScroll(false);
      return;
    }

    const savedScroll = getSavedPageScroll(page);

    if (savedScroll <= 0) {
      setIsRestoringScroll(false);
      return;
    }

    window.scrollTo({ top: savedScroll, left: 0, behavior: "auto" });
    setIsRestoringScroll(false);
  }, [canShowCachedDashboard, page]);

  function setSelectedAuthor(value: string) {
    setSelectedAuthorState(value);
    localStorage.setItem(ACTIVITY_AUTHOR_STORAGE_KEY, value);
  }

  function selectPage(nextPage: Page) {
    const shouldResetScroll = nextPage !== page;

    if (shouldResetScroll) {
      skipNextScrollRestoreRef.current = true;
      setIsRestoringScroll(false);
      sessionStorage.setItem(pageScrollStorageKey(nextPage), "0");
    }

    setPage(nextPage);
    localStorage.setItem(PAGE_STORAGE_KEY, nextPage);

    if (shouldResetScroll) {
      window.requestAnimationFrame(() => {
        window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      });
    }
  }

  async function handleLogout() {
    await apiFetch("/api/v1/auth/logout", { method: "POST" });
    setAuthUser(null);
    setHasAuthHint(false);
    localStorage.removeItem(AUTH_HINT_STORAGE_KEY);
    clearDashboardSessionCaches();
    setSummary(null);
    setHealth(null);
  }

  if (authLoading && !hasAuthHint) {
    return <LoginPage checkingSession onLogin={setAuthUser} />;
  }

  if (!authLoading && !authUser) {
    return <LoginPage onLogin={setAuthUser} />;
  }

  const sessionUser = authUser ?? { email: "", displayName: "Activity Logger", role: "viewer" as const, active: true };

  return (
    <div className={`app-frame${isRestoringScroll ? " restoring-scroll" : ""}`}>
      <aside className="sidebar">
        <div className="brand-mark">
          <img src="/favicon.svg" alt="" aria-hidden="true" />
          <strong>Activity Logger</strong>
        </div>
        <nav className="side-nav">
          <NavButton icon={<UsersRound size={20} />} label="Authors" active={page === "authors"} onClick={() => selectPage("authors")} />
          <NavButton icon={<Activity size={20} />} label="Activity" active={page === "activity"} onClick={() => selectPage("activity")} />
          <NavButton icon={<BarChart3 size={20} />} label="Analytics" active={page === "analytics"} onClick={() => selectPage("analytics")} />
          <NavButton icon={<CalendarDays size={20} />} label="Calendar" active={page === "calendar"} onClick={() => selectPage("calendar")} />
          <NavButton icon={<Bell size={20} />} label="Alerts" active={page === "alerts"} onClick={() => selectPage("alerts")} />
          <NavButton icon={<Settings size={20} />} label="Settings" active={page === "settings"} onClick={() => selectPage("settings")} />
        </nav>
        <div className="session-card sidebar-session-card">
          <span>{sessionUser.displayName}</span>
          <small>{formatSiteRole(sessionUser.role)}</small>
          <button className="icon-button" onClick={() => void handleLogout()} title="Log out">
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-topbar">
          <div className="topbar-title-block">
            <h1>{pageTitle(page)}</h1>
            {!authLoading && loading && pageUsesDashboardSummary(page) ? <span className="topbar-loading-popover">Loading dashboard data...</span> : null}
            <p>{pageSubtitle(page)}</p>
          </div>
          {page === "authors" || page === "activity" || page === "alerts" ? (
            <div className="topbar-actions">
              <DateRangePicker value={dateRange} onChange={setDateRange} />
            </div>
          ) : page === "settings" ? (
            <div className="topbar-actions">
              <span className={health?.ok ? "status-pill online" : "status-pill"}>
                <span className="status-pill-dot" aria-hidden="true" />
                {health?.ok ? "Backend online" : "Backend offline"}
              </span>
            </div>
          ) : null}
        </header>

        {error ? <p className="notice error">{error}</p> : null}

        {page === "authors" ? (
          <AuthorsPage
            authors={authors}
            search={search}
            setSearch={setSearch}
            refreshing={refreshingReports}
            onRefresh={() => void requestReportRefresh()}
          />
        ) : null}
        {page === "activity" ? (
          <ActivityPage
            summary={activitySummary}
            dateRange={appliedDateRange}
            selectedAuthor={activeAuthor}
            setSelectedAuthor={setSelectedAuthor}
            refreshing={refreshingReports}
            onRefreshAuthor={(author) => void requestReportRefresh(author)}
          />
        ) : null}
        {page === "analytics" ? <AnalyticsPage /> : null}
        {page === "calendar" ? <CalendarPage /> : null}
        {page === "alerts" ? <AlertsPage authors={activitySummary.authors} /> : null}
        {page === "settings" ? <SettingsPage summary={canShowCachedDashboard ? summary : null} currentUser={sessionUser} onSaved={() => void load(false)} /> : null}
      </main>
    </div>
  );
}

function dashboardRefreshIntervalMs(summary: Summary | null) {
  const seconds = summary?.intervalSettings.defaultSendIntervalSeconds ?? REFRESH_INTERVAL_MS / 1000;
  return Math.max(1000, seconds * 1000);
}

function summaryViewForPage(page: Page) {
  if (page === "settings") {
    return "settings";
  }

  if (page === "alerts") {
    return "alerts";
  }

  if (page === "activity") {
    return "activity-lite";
  }

  return "authors";
}

function pageUsesDashboardSummary(page: Page) {
  return page === "authors" || page === "activity" || page === "alerts" || page === "settings";
}

function dashboardSummaryCacheKey(page: Page, dateRange: DateRange) {
  const view = summaryViewForPage(page);
  const dateMode = dateRange.preset === "live" ? "authorLocalToday" : "";

  return `${DASHBOARD_SUMMARY_CACHE_PREFIX}${view}.${dateRange.startDate}.${dateRange.endDate}.${dateMode}`;
}

function loadCachedDashboardSummary(page: Page, dateRange: DateRange) {
  if (!pageUsesDashboardSummary(page)) {
    return null;
  }

  try {
    const cached = sessionStorage.getItem(dashboardSummaryCacheKey(page, dateRange));

    if (!cached) {
      return null;
    }

    return JSON.parse(cached) as Summary;
  } catch {
    return null;
  }
}

function saveCachedDashboardSummary(page: Page, dateRange: DateRange, summary: Summary) {
  if (!pageUsesDashboardSummary(page)) {
    return;
  }

  try {
    sessionStorage.setItem(dashboardSummaryCacheKey(page, dateRange), JSON.stringify(summary));
  } catch {
    // Browsers can reject storage writes in private mode or when the quota is full.
  }
}

function clearDashboardSessionCaches() {
  const prefixes = [
    DASHBOARD_SUMMARY_CACHE_PREFIX,
    "AL.Dashboard.ActivityHourly.",
    "AL.Dashboard.ActivityReports.",
    "AL.Dashboard.AnalyticsSummary",
    "AL.Dashboard.CalendarSummary"
  ];

  for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
    const key = sessionStorage.key(index);

    if (key && prefixes.some((prefix) => key.startsWith(prefix))) {
      sessionStorage.removeItem(key);
    }
  }
}

function matchesAuthorSearch(author: AuthorRow, search: string) {
  const query = search.trim().toLowerCase();

  if (!query) {
    return true;
  }

  return [author.displayName, author.authorEmail, author.rawAuthor, author.team, author.source]
    .filter(Boolean)
    .some((value) => value!.toLowerCase().includes(query));
}

function formatSiteRole(role: SiteUserRole) {
  if (role === "admin") {
    return "Admin";
  }

  if (role === "editor") {
    return "Editor";
  }

  return "Viewer";
}

function pageTitle(page: Page) {
  if (page === "activity") {
    return "Activity";
  }

  if (page === "analytics") {
    return "Analytics";
  }

  if (page === "calendar") {
    return "Calendar";
  }

  if (page === "alerts") {
    return "Alerts";
  }

  if (page === "settings") {
    return "Settings";
  }

  return "Authors";
}

function pageSubtitle(page: Page) {
  if (page === "activity") {
    return "Select an author and inspect detailed activity for the selected period.";
  }

  if (page === "analytics") {
    return "Compare author productivity, progress, regressions, and team trends.";
  }

  if (page === "calendar") {
    return "Mark vacation, days off, absences, and notes on the yearly author calendar.";
  }

  if (page === "alerts") {
    return "Author alert stacks and risk signals for the selected period.";
  }

  if (page === "settings") {
    return "Manage author display names, teams, Telegram mapping, and report intervals.";
  }

  return "Team activity overview for the selected period.";
}

function loadSavedPage(): Page {
  const savedPage = localStorage.getItem(PAGE_STORAGE_KEY);

  if (
    savedPage === "activity" ||
    savedPage === "analytics" ||
    savedPage === "calendar" ||
    savedPage === "alerts" ||
    savedPage === "settings" ||
    savedPage === "authors"
  ) {
    return savedPage;
  }

  return "authors";
}

function loadSavedSettingsTab(): SettingsTab {
  const savedTab = localStorage.getItem(SETTINGS_TAB_STORAGE_KEY);

  if (
    savedTab === "general" ||
    savedTab === "authors" ||
    savedTab === "autoBreak" ||
    savedTab === "redirects" ||
    savedTab === "discord" ||
    savedTab === "telegram" ||
    savedTab === "meetingSummaries" ||
    savedTab === "users"
  ) {
    return savedTab;
  }

  return "general";
}

function loadSavedReportsPage() {
  const savedPage = Number(localStorage.getItem(REPORTS_PAGE_STORAGE_KEY) ?? 1);

  if (Number.isInteger(savedPage) && savedPage > 0) {
    return savedPage;
  }

  return 1;
}

function pageScrollStorageKey(page: Page) {
  return `${PAGE_SCROLL_STORAGE_PREFIX}${page}`;
}

function getSavedPageScroll(page: Page) {
  return Number(sessionStorage.getItem(pageScrollStorageKey(page)) ?? 0);
}

function loadSavedActivityAuthor() {
  const savedAuthor = localStorage.getItem(ACTIVITY_AUTHOR_STORAGE_KEY);

  if (savedAuthor && savedAuthor.trim()) {
    return savedAuthor;
  }

  return null;
}

function loadSavedDateRange(): DateRange {
  const savedRange = localStorage.getItem(DATE_RANGE_STORAGE_KEY);

  if (!savedRange) {
    return todayRange();
  }

  try {
    const parsed = JSON.parse(savedRange) as Partial<DateRange>;

    if (parsed.preset === "live") {
      return todayRange();
    }

    if (
      (parsed.preset === "yesterday" || parsed.preset === "custom") &&
      isDateInputValue(parsed.startDate) &&
      isDateInputValue(parsed.endDate)
    ) {
      return {
        startDate: parsed.startDate,
        endDate: parsed.startDate,
        preset: parsed.preset
      };
    }
  } catch {
    return todayRange();
  }

  return todayRange();
}

function todayRange(): DateRange {
  const today = toDateInputValue(new Date());
  return { startDate: today, endDate: today, preset: "live" };
}

function yesterdayRange(): DateRange {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const date = toDateInputValue(yesterday);
  return { startDate: date, endDate: date, preset: "yesterday" };
}

function toDateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function isDateInputValue(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
}

export default App;
