import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, BarChart3, Bell, Box, CalendarDays, LogOut, RefreshCw, Search, Settings, ShieldCheck, UsersRound } from "lucide-react";
import { AuthorsTable } from "./components/AuthorsTable";
import { AnalyticsActivityOverview } from "./components/AnalyticsActivityOverview";
import { HourlyActivityChart } from "./components/HourlyActivityChart";
import cursorIconUrl from "./assets/cursor-icon.png";
import "./styles.css";

const LOCAL_HOSTNAMES = new Set(["127.0.0.1", "localhost"]);
const IS_LOCAL_DASHBOARD = LOCAL_HOSTNAMES.has(window.location.hostname);
const API_URL = import.meta.env.VITE_API_URL ?? (IS_LOCAL_DASHBOARD ? "http://127.0.0.1:8000" : "https://activity.mempic.com");
const REFRESH_INTERVAL_MS = 10000;
const PAGE_STORAGE_KEY = "AL.Dashboard.Page";
const ACTIVITY_AUTHOR_STORAGE_KEY = "AL.Dashboard.ActivityAuthor";
const AUTH_HINT_STORAGE_KEY = "AL.Dashboard.Authenticated";

function apiFetch(path: string, init: RequestInit = {}) {
  return fetch(`${API_URL}${path}`, { ...init, credentials: "include" });
}

type Page = "authors" | "activity" | "analytics" | "calendar" | "alerts" | "settings";

type Health = {
  ok: boolean;
  mongo: boolean;
};

type Report = {
  source?: string;
  author?: string;
  displayName?: string;
  team?: string;
  date?: string;
  activeDeltaSeconds?: number;
  idleDeltaSeconds?: number;
  overtimeActiveDeltaSeconds?: number;
  recordedAt?: string;
  receivedAt?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  reportType?: string;
  activityType?: string;
  telegramEventType?: string;
  telegramStatus?: string;
  discordEventType?: string;
  discordStatus?: string;
  pluginVersion?: string;
};

type AuthorRow = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  telegramUsername?: string;
  discordUserId?: string;
  discordUsername?: string;
  authorColor?: string;
  source?: string;
  pluginVersion?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  lastRecordedAt?: string;
  lastReceivedAt?: string;
  daySeconds: number;
  telegramDaySeconds: number;
  pluginDaySeconds: number;
  telegramToFirstActivitySeconds?: number;
  activeSeconds: number;
  idleSeconds: number;
  meetingSeconds: number;
  breakSeconds: number;
  overtimeActiveSeconds: number;
  rawPluginDaySeconds?: number;
  productivity: number;
  activityMix?: ActivityCount[];
  savedPrefabs?: SavedPrefab[];
  overtimeActivityMix?: ActivityCount[];
  overtimeSavedPrefabs?: SavedPrefab[];
  status?: "online" | "stale";
  stalePresence?: "telegram" | "reports" | "both";
  alerts?: AuthorAlert[];
  alertStats?: AlertStats;
};

type AuthorAlert = {
  id?: string;
  type: string;
  severity: "critical" | "warning";
  title: string;
  message: string;
  value?: number | null;
  threshold?: number | null;
  source?: string;
  pluginVersion?: string;
  deviceId?: string;
  challengeId?: string;
  createdAt?: string;
};

type AlertStats = {
  total: number;
  critical: number;
  warning: number;
};

type ActivitySummary = {
  totals: {
    daySeconds: number;
    telegramDaySeconds: number;
    pluginDaySeconds: number;
    rawPluginDaySeconds?: number;
    telegramToFirstActivitySeconds?: number;
    activeSeconds: number;
    idleSeconds: number;
    meetingSeconds: number;
    breakSeconds: number;
    overtimeActiveSeconds: number;
  };
  authors: AuthorRow[];
  profiles: AuthorProfile[];
  authorAliases?: AuthorAlias[];
  activityMix: ActivityCount[];
  savedPrefabs: SavedPrefab[];
  overtimeActivityMix?: ActivityCount[];
  overtimeSavedPrefabs?: SavedPrefab[];
  hourlyActivityByAuthor: AuthorHourlyActivity[];
};

type AuthorAlias = {
  sourceRawAuthor: string;
  targetRawAuthor: string;
};

type AuthorProfile = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  telegramUsername?: string;
  discordUserId?: string;
  discordUsername?: string;
  pluginEnabled?: boolean;
  authorColor?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
};

type ActivityCount = {
  type: string;
  count: number;
  percent: number;
};

type SavedPrefab = {
  path: string;
  name: string;
  projectId?: string;
  saveCount: number;
};

type HourlyActivity = {
  hour: number;
  activeSeconds: number;
  idleSeconds: number;
  breakSeconds?: number;
  meetingSeconds?: number;
  overtimeActiveSeconds?: number;
};

type AuthorHourlyActivity = {
  author: string;
  rawAuthor?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  hourlyActivity: HourlyActivity[];
};

type Summary = {
  authors: string[];
  reports: Report[];
  intervalSettings: {
    defaultSendIntervalSeconds: number;
    authors: Array<{ author: string; sendIntervalSeconds: number }>;
  };
  activitySummary: ActivitySummary;
};

type ReportsPage = {
  reports: Report[];
  total: number;
  limit: number;
  offset: number;
  sources: string[];
};

type ReportsPageCache = Record<string, ReportsPage>;

type SiteUserRole = "admin" | "editor" | "viewer";

type SiteUser = {
  email: string;
  displayName: string;
  role: SiteUserRole;
  active: boolean;
};

type AnalyticsTotals = {
  daySeconds: number;
  activeSeconds: number;
  idleSeconds: number;
  meetingSeconds: number;
  overtimeActiveSeconds: number;
  breakSeconds: number;
  pluginDaySeconds: number;
  telegramDaySeconds: number;
  productivity: number;
};

type AnalyticsAuthorSummary = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  authorColor?: string;
  months: AnalyticsMonth[];
};

type AnalyticsSummary = {
  year: number;
  authors: AnalyticsAuthorSummary[];
};

type AnalyticsMonth = {
  month: number;
  label: string;
  startDate: string;
  endDate: string;
  totals: AnalyticsTotals;
  previousMonthDeltas: AnalyticsDelta;
  weeks: AnalyticsWeek[];
};

type AnalyticsWeek = {
  week: number;
  label: string;
  startDate: string;
  endDate: string;
  totals: AnalyticsTotals;
  previousWeekDeltas: AnalyticsDelta;
  days: AnalyticsDay[];
};

type AnalyticsDay = {
  date: string;
  label: string;
  inMonth: boolean;
  totals: AnalyticsTotals;
  hourlyActivity: HourlyActivity[];
};

type AnalyticsDelta = {
  activeSeconds: number;
  idleSeconds: number;
  meetingSeconds: number;
  overtimeActiveSeconds: number;
  breakSeconds: number;
  pluginDaySeconds: number;
  telegramDaySeconds: number;
  productivity: number;
};

type CalendarAuthor = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  authorColor: string;
};

type CalendarReason = {
  id: string;
  label: string;
};

type CalendarMark = {
  rawAuthor: string;
  displayName: string;
  authorColor: string;
  date: string;
  reasonId: string;
  reasonLabel: string;
  note: string;
};

type CalendarAuthorStats = {
  rawAuthor: string;
  displayName: string;
  authorColor: string;
  totalMarkedDays: number;
  byReason: Record<string, number>;
  latestMarks: CalendarMark[];
};

type CalendarSummary = {
  year: number;
  authors: CalendarAuthor[];
  reasons: CalendarReason[];
  marks: CalendarMark[];
  stats: CalendarAuthorStats[];
};

type DateRange = {
  startDate: string;
  endDate: string;
  preset: "live" | "yesterday" | "custom";
};

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
  const [authUser, setAuthUser] = useState<SiteUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [hasAuthHint, setHasAuthHint] = useState(() => localStorage.getItem(AUTH_HINT_STORAGE_KEY) === "true");
  const [health, setHealth] = useState<Health | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [dateRange, setDateRange] = useState<DateRange>(() => todayRange());
  const [appliedDateRange, setAppliedDateRange] = useState<DateRange>(() => todayRange());
  const [search, setSearch] = useState("");
  const [selectedAuthor, setSelectedAuthorState] = useState<string | null>(() => loadSavedActivityAuthor());
  const [loading, setLoading] = useState(true);
  const [refreshingReports, setRefreshingReports] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

    try {
      const params = new URLSearchParams({
        startDate: requestedDateRange.startDate,
        endDate: requestedDateRange.endDate
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
      setSummary(await summaryResponse.json());
      setAppliedDateRange(requestedDateRange);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [authUser?.email, dateRange.startDate, dateRange.endDate, dateRange.preset]);

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
  }, [authUser?.email, dateRange.startDate, dateRange.endDate, dateRange.preset, dashboardRefreshMs]);

  const activitySummary = summary?.activitySummary ?? emptyActivitySummary;
  const authors = useMemo(() => activitySummary.authors.filter((author) => matchesAuthorSearch(author, search)), [activitySummary, search]);
  const activeAuthor = activitySummary.authors.some((author) => author.rawAuthor === selectedAuthor)
    ? selectedAuthor
    : authors[0]?.rawAuthor ?? activitySummary.authors[0]?.rawAuthor ?? null;

  useEffect(() => {
    if (!activeAuthor && activitySummary.authors.length) {
      setSelectedAuthor(activitySummary.authors[0].rawAuthor);
    }
  }, [activeAuthor, activitySummary.authors]);

  function setSelectedAuthor(value: string) {
    setSelectedAuthorState(value);
    localStorage.setItem(ACTIVITY_AUTHOR_STORAGE_KEY, value);
  }

  function selectPage(nextPage: Page) {
    setPage(nextPage);
    localStorage.setItem(PAGE_STORAGE_KEY, nextPage);
  }

  async function handleLogout() {
    await apiFetch("/api/v1/auth/logout", { method: "POST" });
    setAuthUser(null);
    setHasAuthHint(false);
    localStorage.removeItem(AUTH_HINT_STORAGE_KEY);
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
    <div className="app-frame">
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
            {!authLoading && loading ? <span className="topbar-loading-popover">Loading dashboard data...</span> : null}
            <p>{pageSubtitle(page)}</p>
          </div>
          {page === "authors" || page === "activity" || page === "alerts" ? (
            <div className="topbar-actions">
              <DateRangePicker value={dateRange} onChange={setDateRange} />
            </div>
          ) : null}
        </header>

        {authLoading ? <p className="notice">Restoring dashboard session...</p> : null}
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
        {page === "settings" ? <SettingsPage summary={summary} health={health} currentUser={sessionUser} onSaved={() => void load(false)} /> : null}
      </main>
    </div>
  );
}

function LoginPage({ checkingSession = false, onLogin }: { checkingSession?: boolean; onLogin: (user: SiteUser) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const response = await apiFetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
      });

      if (!response.ok) {
        throw new Error(loginErrorMessage(response.status));
      }

      const payload = await response.json();
      onLogin(payload.user);
    } catch (requestError) {
      if (requestError instanceof Error && !isNetworkLoginError(requestError)) {
        setError(requestError.message);
      } else {
        setError(BACKEND_OFFLINE_MESSAGE);
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-hero">
        <img className="login-logo" src="/logo.png" alt="Mempic Game Studio" />
        <p className="eyebrow">Activity Logger</p>
        <h1>Welcome to the team ride control room.</h1>
        <p>
          Track Unity and Blender activity, spot stalled reports, and keep the production sprint moving from one focused dashboard.
        </p>
      </section>
      <form className="login-card" onSubmit={(event) => void submit(event)}>
        <div className="login-card-icon">
          <ShieldCheck size={28} />
        </div>
        <h2>Sign in</h2>
        <p>Use the email and password issued by your site administrator.</p>
        <label>
          Email
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" required />
        </label>
        <label>
          Password
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        {checkingSession ? <p className="notice">Checking session...</p> : null}
        {error ? <p className="notice error">{error}</p> : null}
        <button className="primary-button" type="submit" disabled={checkingSession || submitting}>
          {checkingSession ? "Checking..." : submitting ? "Signing in..." : "Enter dashboard"}
        </button>
      </form>
    </main>
  );
}

const BACKEND_OFFLINE_MESSAGE = "Backend is still offline after deploy. Please wait a moment and reload the page.";

function loginErrorMessage(status: number) {
  if (status === 401 || status === 403) {
    return "Invalid email or password";
  }

  if (status >= 500 || status === 0) {
    return BACKEND_OFFLINE_MESSAGE;
  }

  return "Login failed. Please try again.";
}

function isNetworkLoginError(error: Error) {
  return error instanceof TypeError || /failed to fetch|networkerror|load failed/i.test(error.message);
}

function NavButton({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void }) {
  return (
    <button className={active ? "side-nav-item active" : "side-nav-item"} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  );
}

function AuthorsPage({
  authors,
  search,
  setSearch,
  refreshing,
  onRefresh
}: {
  authors: AuthorRow[];
  search: string;
  setSearch: (value: string) => void;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const sortedAuthors = [...authors].sort(compareAuthorsByStatusAndProductivity);

  return (
    <section className="page-section">
      <div className="toolbar">
        <div className="search-box">
          <Search size={18} />
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search authors" />
        </div>
        <div className="toolbar-spacer" />
        <button className="primary-outline-button" onClick={onRefresh} disabled={refreshing}>
          <RefreshCw size={16} />
          {refreshing ? "Requesting..." : "Refresh"}
        </button>
      </div>

      <AuthorsTable authors={sortedAuthors} emptyMessage="No authors match this search." />
    </section>
  );
}

function AnalyticsPage() {
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
    const intervalId = window.setInterval(() => void loadAnalytics(false), REFRESH_INTERVAL_MS);

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

function CalendarPage() {
  const year = new Date().getFullYear();
  const [calendar, setCalendar] = useState<CalendarSummary | null>(null);
  const [selectedAuthor, setSelectedAuthor] = useState("all");
  const [selectedDates, setSelectedDates] = useState<string[]>([]);
  const [rangeStart, setRangeStart] = useState<string | null>(null);
  const [rangeMode, setRangeMode] = useState(false);
  const [showMarkEditor, setShowMarkEditor] = useState(false);
  const [showClearEditor, setShowClearEditor] = useState(false);
  const [markAuthors, setMarkAuthors] = useState<string[]>([]);
  const [clearAuthors, setClearAuthors] = useState<string[]>([]);
  const [markReason, setMarkReason] = useState("");
  const [markNote, setMarkNote] = useState("");
  const [reasonEditId, setReasonEditId] = useState<string | null>(null);
  const [reasonLabel, setReasonLabel] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadCalendar(showLoading = true) {
    if (showLoading) {
      setLoading(true);
    }

    try {
      const response = await apiFetch(`/api/v1/calendar/summary?year=${year}`);

      if (!response.ok) {
        throw new Error("Calendar request failed");
      }

      const data: CalendarSummary = await response.json();
      setCalendar(data);
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load calendar.");
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    void loadCalendar();
    const intervalId = window.setInterval(() => void loadCalendar(false), REFRESH_INTERVAL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, []);

  const visibleMarks = (calendar?.marks ?? []).filter((mark) => selectedAuthor === "all" || mark.rawAuthor === selectedAuthor);
  const visibleStats = (calendar?.stats ?? []).filter((stat) => selectedAuthor === "all" || stat.rawAuthor === selectedAuthor);
  const marksByDate = visibleMarks.reduce<Record<string, CalendarMark[]>>((items, mark) => {
    items[mark.date] = [...(items[mark.date] ?? []), mark];
    return items;
  }, {});

  function toggleDate(date: string, shiftKey = false) {
    if ((rangeMode || shiftKey) && rangeStart) {
      setSelectedDates(uniqueDates([...selectedDates, ...dateRangeList(rangeStart, date)]));
      setRangeStart(null);
      return;
    }

    if (rangeMode || shiftKey) {
      setRangeStart(date);
      setSelectedDates(uniqueDates([...selectedDates, date]));
      return;
    }

    setSelectedDates((items) => (items.includes(date) ? items.filter((item) => item !== date) : [...items, date].sort()));
  }

  function openMarkEditor() {
    if (!calendar || !selectedDates.length) {
      return;
    }

    setMarkAuthors(selectedAuthor === "all" ? [] : [selectedAuthor]);
    setMarkReason("");
    setMarkNote("");
    setShowMarkEditor(true);
  }

  function openMarkEditorForReason(reasonId: string) {
    if (!calendar || !selectedDates.length) {
      setError("Select day or days before applying a reason.");
      return;
    }

    setMarkAuthors(selectedAuthor === "all" ? [] : [selectedAuthor]);
    setMarkReason(reasonId);
    setMarkNote("");
    setShowMarkEditor(true);
    setError(null);
  }

  function openClearEditor() {
    if (!calendar || !selectedDates.length) {
      return;
    }

    setClearAuthors(selectedAuthor === "all" ? [] : [selectedAuthor]);
    setShowClearEditor(true);
    setError(null);
  }

  async function saveMarks() {
    if (!markAuthors.length || !selectedDates.length || !markReason || !markNote.trim()) {
      setError("Select authors, dates, reason, and note.");
      return;
    }

    const response = await apiFetch(`/api/v1/calendar/marks`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ authors: markAuthors, dates: selectedDates, reasonId: markReason, note: markNote.trim() })
    });

    if (!response.ok) {
      setError("Calendar mark save failed.");
      return;
    }

    setShowMarkEditor(false);
    setMarkReason("");
    setSelectedDates([]);
    await loadCalendar(false);
  }

  async function clearMarks() {
    if (!clearAuthors.length || !selectedDates.length) {
      setError("Select authors and dates to clear.");
      return;
    }

    for (const author of clearAuthors) {
      for (const date of selectedDates) {
        const params = new URLSearchParams({ author, date });
        const response = await apiFetch(`/api/v1/calendar/marks?${params.toString()}`, { method: "DELETE" });

        if (!response.ok) {
          setError("Calendar mark delete failed.");
          return;
        }
      }
    }

    setShowClearEditor(false);
    setSelectedDates([]);
    await loadCalendar(false);
  }

  async function saveReason() {
    if (!reasonLabel.trim()) {
      return;
    }

    const response = await apiFetch(`/api/v1/calendar/reasons`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: reasonEditId, label: reasonLabel.trim() })
    });

    if (!response.ok) {
      setError("Reason save failed.");
      return;
    }

    setReasonLabel("");
    setReasonEditId(null);
    await loadCalendar(false);
  }

  return (
    <section className="page-section calendar-page">
      {error ? <p className="notice error">{error}</p> : null}
      {calendar ? (
        <>
          <div className="calendar-year-header">
            <div>
              <span>Current date</span>
              <strong>{new Date().toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" })}</strong>
            </div>
            <div>
              <span>Calendar year</span>
              <strong>{calendar.year}</strong>
            </div>
          </div>

          <div className="author-card-strip calendar-author-strip">
            <button className={selectedAuthor === "all" ? "author-card active" : "author-card"} onClick={() => setSelectedAuthor("all")}>
              <span className="avatar-stack" aria-hidden="true">
                {calendar.authors.slice(0, 5).map((author) => (
                  <span className="avatar mini-avatar" style={{ background: author.authorColor }} key={author.rawAuthor}>{initials(author.displayName)}</span>
                ))}
              </span>
              <strong>All authors</strong>
              <small>Show all marks</small>
            </button>
            {calendar.authors.map((author) => (
              <button className={selectedAuthor === author.rawAuthor ? "author-card active" : "author-card"} key={author.rawAuthor} onClick={() => setSelectedAuthor(author.rawAuthor)}>
                <span className="avatar" style={{ background: author.authorColor }}>{initials(author.displayName)}</span>
                <strong>{author.displayName}</strong>
                <small>{author.team || "No team"}</small>
              </button>
            ))}
          </div>

          <div className="calendar-management">
            <div className="calendar-toolbar">
              <strong>{selectedDates.length} selected days</strong>
              <button className={rangeMode ? "primary-button" : "primary-outline-button"} onClick={() => setRangeMode((value) => !value)}>Range select</button>
              <button className="primary-outline-button" onClick={() => { setSelectedDates([]); setRangeStart(null); }}>Clear selection</button>
              <button className="primary-button" onClick={openMarkEditor} disabled={!selectedDates.length}>Mark days</button>
              <button className="primary-outline-button danger-button" onClick={openClearEditor} disabled={!selectedDates.length}>Clear marks</button>
            </div>
            <ReasonEditor
              reasons={calendar.reasons}
              reasonLabel={reasonLabel}
              setReasonLabel={setReasonLabel}
              setReasonEditId={setReasonEditId}
              onPickReason={openMarkEditorForReason}
              onSave={() => void saveReason()}
            />
          </div>

          <div className="year-calendar-panel">
            <div className="year-calendar">
              {monthIndexes().map((month) => (
                <MonthCalendar
                  year={year}
                  month={month}
                  marksByDate={marksByDate}
                  selectedDates={selectedDates}
                  rangeStart={rangeStart}
                  onSelect={toggleDate}
                  key={month}
                />
              ))}
            </div>
            <CalendarLegend authors={selectedAuthor === "all" ? calendar.authors : calendar.authors.filter((author) => author.rawAuthor === selectedAuthor)} reasons={calendar.reasons} />
          </div>

          <CalendarStats stats={visibleStats} reasons={calendar.reasons} />

          {showMarkEditor ? (
            <CalendarMarkEditor
              authors={calendar.authors}
              selectedAuthor={selectedAuthor}
              selectedAuthors={markAuthors}
              setSelectedAuthors={setMarkAuthors}
              reasons={calendar.reasons}
              reasonId={markReason}
              setReasonId={setMarkReason}
              note={markNote}
              setNote={setMarkNote}
              selectedDates={selectedDates}
              onCancel={() => setShowMarkEditor(false)}
              onSave={() => void saveMarks()}
            />
          ) : null}
          {showClearEditor ? (
            <CalendarClearEditor
              authors={calendar.authors}
              selectedAuthor={selectedAuthor}
              selectedAuthors={clearAuthors}
              setSelectedAuthors={setClearAuthors}
              selectedDates={selectedDates}
              onCancel={() => setShowClearEditor(false)}
              onClear={() => void clearMarks()}
            />
          ) : null}
        </>
      ) : loading ? (
        <p className="notice">Loading calendar...</p>
      ) : (
        <p className="empty">No calendar data yet.</p>
      )}
    </section>
  );
}

function MonthCalendar({
  year,
  month,
  marksByDate,
  selectedDates,
  rangeStart,
  onSelect
}: {
  year: number;
  month: number;
  marksByDate: Record<string, CalendarMark[]>;
  selectedDates: string[];
  rangeStart: string | null;
  onSelect: (date: string, shiftKey: boolean) => void;
}) {
  const firstDay = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const leadingDays = (firstDay.getDay() + 6) % 7;
  const monthName = firstDay.toLocaleString(undefined, { month: "long" });

  return (
    <section className="month-card">
      <h2>{monthName}</h2>
      <div className="month-weekdays">
        {["M", "T", "W", "T", "F", "S", "S"].map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}
      </div>
      <div className="month-grid">
        {Array.from({ length: leadingDays }).map((_, index) => <span className="calendar-day empty-day" key={`empty-${index}`} />)}
        {Array.from({ length: daysInMonth }).map((_, index) => {
          const day = index + 1;
          const date = toCalendarDate(year, month, day);
          const marks = marksByDate[date] ?? [];
          const selected = selectedDates.includes(date);
          const today = toDateInputValue(new Date());
          const isToday = date === today;
          const isPast = date < today;
          const title = marks.map((mark) => `${mark.displayName}: ${mark.reasonLabel} - ${mark.note}`).join("\n");

          return (
            <button
              className={calendarDayClassName(selected, isToday, isPast)}
              disabled={isPast}
              key={date}
              title={title || (isPast ? `${date} is locked` : date)}
              onClick={(event) => onSelect(date, event.shiftKey)}
            >
              <span>{day}</span>
              {isToday ? <strong>Today</strong> : null}
              {marks.length ? (
                <span className="day-mark-stack">
                  {marks.slice(0, 4).map((mark) => <i style={{ background: mark.authorColor }} key={`${mark.rawAuthor}-${mark.reasonId}`} />)}
                </span>
              ) : null}
              {rangeStart === date ? <span className="range-dot" /> : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}

function CalendarLegend({ reasons }: { authors: CalendarAuthor[]; reasons: CalendarReason[] }) {
  return (
    <div className="calendar-legend">
      <div className="reason-list">
        {reasons.map((reason) => <span key={reason.id}>{reason.label}</span>)}
      </div>
    </div>
  );
}

function ReasonEditor({
  reasons,
  reasonLabel,
  setReasonLabel,
  setReasonEditId,
  onPickReason,
  onSave
}: {
  reasons: CalendarReason[];
  reasonLabel: string;
  setReasonLabel: (value: string) => void;
  setReasonEditId: (value: string | null) => void;
  onPickReason: (reasonId: string) => void;
  onSave: () => void;
}) {
  return (
    <div className="calendar-reasons">
      <h2>Reasons</h2>
      <div className="reason-list">
        {reasons.map((reason) => (
          <button
            className="reason-chip-button"
            key={reason.id}
            onClick={() => onPickReason(reason.id)}
            title="Apply this reason to selected days"
          >
            {reason.label}
          </button>
        ))}
      </div>
      <div className="reason-editor">
        <input
          value={reasonLabel}
          onChange={(event) => {
            setReasonEditId(null);
            setReasonLabel(event.target.value);
          }}
          placeholder="New reason label"
        />
        <button className="primary-outline-button" onClick={onSave} disabled={!reasonLabel.trim()}>Save reason</button>
      </div>
      <p className="calendar-helper">Select days, then click a reason chip to mark them.</p>
    </div>
  );
}

function CalendarStats({ stats, reasons }: { stats: CalendarAuthorStats[]; reasons: CalendarReason[] }) {
  return (
    <div className="calendar-stats-grid">
      {stats.map((stat) => (
        <article className="calendar-stat-card" key={stat.rawAuthor}>
          <div>
            <span className="color-dot" style={{ background: stat.authorColor }} />
            <strong>{stat.displayName}</strong>
          </div>
          <span>{stat.totalMarkedDays} marked days</span>
          <div className="stat-reasons">
            {reasons.map((reason) => <small key={reason.id}>{reason.label}: {stat.byReason[reason.id] ?? 0}</small>)}
          </div>
          <div className="latest-mark-stack">
            {stat.latestMarks.length ? stat.latestMarks.map((mark) => (
              <small key={`${mark.date}-${mark.reasonId}`}>{mark.date}: {mark.reasonLabel}</small>
            )) : <small>No marks yet.</small>}
          </div>
        </article>
      ))}
    </div>
  );
}

function CalendarMarkEditor({
  authors,
  selectedAuthor,
  selectedAuthors,
  setSelectedAuthors,
  reasons,
  reasonId,
  setReasonId,
  note,
  setNote,
  selectedDates,
  onCancel,
  onSave
}: {
  authors: CalendarAuthor[];
  selectedAuthor: string;
  selectedAuthors: string[];
  setSelectedAuthors: (value: string[]) => void;
  reasons: CalendarReason[];
  reasonId: string;
  setReasonId: (value: string) => void;
  note: string;
  setNote: (value: string) => void;
  selectedDates: string[];
  onCancel: () => void;
  onSave: () => void;
}) {
  function toggleAuthor(rawAuthor: string) {
    setSelectedAuthors(selectedAuthors.includes(rawAuthor) ? selectedAuthors.filter((item) => item !== rawAuthor) : [...selectedAuthors, rawAuthor]);
  }

  return (
    <div className="modal-backdrop">
      <div className="calendar-modal">
        <h2>Mark {selectedDates.length} days</h2>
        <div className="modal-author-list">
          {authors.map((author) => (
            <label key={author.rawAuthor}>
              <input
                type="checkbox"
                checked={selectedAuthors.includes(author.rawAuthor)}
                disabled={selectedAuthor !== "all" && selectedAuthor !== author.rawAuthor}
                onChange={() => toggleAuthor(author.rawAuthor)}
              />
              <span className="color-dot" style={{ background: author.authorColor }} />
              {author.displayName}
            </label>
          ))}
        </div>
        <label>
          Reason
          <select value={reasonId} onChange={(event) => setReasonId(event.target.value)}>
            <option value="">Select reason</option>
            {reasons.map((reason) => <option value={reason.id} key={reason.id}>{reason.label}</option>)}
          </select>
        </label>
        <label>
          Note
          <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="Required note" />
        </label>
        <div className="modal-actions">
          <button className="primary-outline-button" onClick={onCancel}>Cancel</button>
          <button className="primary-button" onClick={onSave} disabled={!selectedAuthors.length || !note.trim() || !reasonId}>Save marks</button>
        </div>
      </div>
    </div>
  );
}

function CalendarClearEditor({
  authors,
  selectedAuthor,
  selectedAuthors,
  setSelectedAuthors,
  selectedDates,
  onCancel,
  onClear
}: {
  authors: CalendarAuthor[];
  selectedAuthor: string;
  selectedAuthors: string[];
  setSelectedAuthors: (value: string[]) => void;
  selectedDates: string[];
  onCancel: () => void;
  onClear: () => void;
}) {
  function toggleAuthor(rawAuthor: string) {
    setSelectedAuthors(selectedAuthors.includes(rawAuthor) ? selectedAuthors.filter((item) => item !== rawAuthor) : [...selectedAuthors, rawAuthor]);
  }

  return (
    <div className="modal-backdrop">
      <div className="calendar-modal">
        <h2>Clear marks for {selectedDates.length} days</h2>
        <p className="calendar-helper">This removes saved marks for selected dates and selected authors.</p>
        <div className="modal-author-list">
          {authors.map((author) => (
            <label key={author.rawAuthor}>
              <input
                type="checkbox"
                checked={selectedAuthors.includes(author.rawAuthor)}
                disabled={selectedAuthor !== "all" && selectedAuthor !== author.rawAuthor}
                onChange={() => toggleAuthor(author.rawAuthor)}
              />
              <span className="color-dot" style={{ background: author.authorColor }} />
              {author.displayName}
            </label>
          ))}
        </div>
        <div className="modal-actions">
          <button className="primary-outline-button" onClick={onCancel}>Cancel</button>
          <button className="primary-button danger-solid-button" onClick={onClear} disabled={!selectedAuthors.length}>Clear marks</button>
        </div>
      </div>
    </div>
  );
}

function AlertsPage({ authors }: { authors: AuthorRow[] }) {
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
                <span className="avatar" style={avatarStyle(author.authorColor)}>{initials(author.displayName)}</span>
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

function AlertSummaryMetric({ label, value, tone }: { label: string; value: number; tone: "critical" | "warning" | "healthy" | "neutral" }) {
  return (
    <div className={`alert-summary-metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ActivityPage({
  summary,
  dateRange,
  selectedAuthor,
  setSelectedAuthor,
  refreshing,
  onRefreshAuthor
}: {
  summary: ActivitySummary;
  dateRange: DateRange;
  selectedAuthor: string | null;
  setSelectedAuthor: (value: string) => void;
  refreshing: boolean;
  onRefreshAuthor: (author: string) => void;
}) {
  const author = summary.authors.find((item) => item.rawAuthor === selectedAuthor) ?? summary.authors[0];
  const hourly = summary.hourlyActivityByAuthor
    .filter((item) => item.rawAuthor === author?.rawAuthor)
    .map((item) => ({ ...item, status: author?.status }));
  const authorHourly = hourly.length || !author
    ? hourly
    : [{ author: author.displayName, rawAuthor: author.rawAuthor, status: author.status, hourlyActivity: [] }];
  const activityMix = author?.activityMix ?? [];
  const savedPrefabs = author?.savedPrefabs ?? [];
  const overtimeActivityMix = author?.overtimeActivityMix ?? [];
  const overtimeSavedPrefabs = author?.overtimeSavedPrefabs ?? [];
  const cardAuthors = [...summary.authors].sort((left, right) => compareAuthorCardStatus(left, right, dateRange));
  const [reports, setReports] = useState<Report[]>([]);
  const [reportsTotal, setReportsTotal] = useState(0);
  const [reportSources, setReportSources] = useState<string[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [reportsError, setReportsError] = useState<string | null>(null);
  const [reportsPageSize, setReportsPageSize] = useState(10);
  const [reportsPage, setReportsPage] = useState(1);
  const [reportSourceFilter, setReportSourceFilter] = useState("");
  const [reportsPageCache, setReportsPageCache] = useState<ReportsPageCache>({});
  const reportsCacheKey = useMemo(() => JSON.stringify({
    author: author?.rawAuthor ?? "",
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    dateMode: dateRange.preset === "live" ? "authorLocalToday" : "",
    source: reportSourceFilter,
    limit: reportsPageSize,
    page: reportsPage
  }), [author?.rawAuthor, dateRange.startDate, dateRange.endDate, dateRange.preset, reportSourceFilter, reportsPageSize, reportsPage]);

  useEffect(() => {
    setReportsPage(1);
  }, [author?.rawAuthor, dateRange.startDate, dateRange.endDate, dateRange.preset, reportsPageSize, reportSourceFilter]);

  useEffect(() => {
    let ignore = false;

    async function loadReports() {
      if (!author?.rawAuthor) {
        setReports([]);
        setReportsTotal(0);
        setReportSources([]);
        return;
      }

      const cachedPage = reportsPageCache[reportsCacheKey];

      if (cachedPage) {
        setReports(cachedPage.reports);
        setReportsTotal(cachedPage.total);
        setReportSources(cachedPage.sources);
        setReportsLoading(false);
        setReportsError(null);
        return;
      }

      setReportsLoading(true);
      setReportsError(null);

      const params = new URLSearchParams({
        startDate: dateRange.startDate,
        endDate: dateRange.endDate,
        author: author.rawAuthor,
        limit: String(reportsPageSize),
        offset: String((reportsPage - 1) * reportsPageSize)
      });

      if (dateRange.preset === "live") {
        params.set("dateMode", "authorLocalToday");
      }

      if (reportSourceFilter) {
        params.set("source", reportSourceFilter);
      }

      try {
        const response = await apiFetch(`/api/v1/reports/table?${params.toString()}`);

        if (!response.ok) {
          throw new Error("Reports request failed");
        }

        const payload = (await response.json()) as ReportsPage;

        if (ignore) {
          return;
        }

        setReports(payload.reports);
        setReportsTotal(payload.total);
        setReportSources(payload.sources);
        setReportsPageCache((current) => ({
          ...current,
          [reportsCacheKey]: payload
        }));
      } catch (requestError) {
        if (ignore) {
          return;
        }

        setReports([]);
        setReportsTotal(0);
        setReportSources([]);
        setReportsError(requestError instanceof Error ? requestError.message : "Unknown error");
      } finally {
        if (!ignore) {
          setReportsLoading(false);
        }
      }
    }

    void loadReports();

    return () => {
      ignore = true;
    };
  }, [author?.rawAuthor, dateRange.startDate, dateRange.endDate, dateRange.preset, reportsPage, reportsPageSize, reportSourceFilter, reportsCacheKey, reportsPageCache]);

  return (
    <section className="page-section">
      <div className="author-card-strip">
        {cardAuthors.map((item) => (
          <button
            className={authorCardClassName(item, item.rawAuthor === author?.rawAuthor)}
            key={item.rawAuthor}
            onClick={() => setSelectedAuthor(item.rawAuthor)}
          >
            <span className="author-card-status" aria-hidden="true" />
            <span className="author-card-identity">
              <span className="avatar" style={avatarStyle(item.authorColor)}>{initials(item.displayName)}</span>
              {productivityTone(item.productivity) === "overdrive" ? <span className="overdrive-author-text">Are you human?</span> : null}
            </span>
            <strong>{item.displayName}</strong>
            <small>{item.team || "No team"}</small>
            <div className="author-card-footer">
              <div className="mini-metrics">
                <span>{formatDuration(item.activeSeconds)} active</span>
                <span>{formatDuration(item.idleSeconds)} idle</span>
                <span>{formatDuration(item.breakSeconds)} break</span>
              </div>
              <div className={`productivity-badge ${authorCardProductivityTone(item)}`}>
                <strong>{item.productivity.toFixed(0)}%</strong>
              </div>
            </div>
          </button>
        ))}
      </div>

      {author ? (
        <>
          <div className="toolbar">
            <div>
              <strong>{author.displayName}</strong>
              <p className="toolbar-caption">Request a fresh Unity report for this author.</p>
            </div>
            <div className="toolbar-spacer" />
            <button className="primary-outline-button" onClick={() => onRefreshAuthor(author.rawAuthor)} disabled={refreshing}>
              <RefreshCw size={16} />
              {refreshing ? "Requesting..." : "Refresh"}
            </button>
          </div>

          <AuthorsTable authors={[author]} emptyMessage="No selected author activity for this period." />

          <div className="activity-grid">
            <Duration label="Day Time (Telegram)" seconds={author.telegramDaySeconds ?? author.daySeconds} />
            <Duration label="Telegram vs FirstActivity" seconds={author.telegramToFirstActivitySeconds ?? 0} />
            <Duration label="Day Time (Plugin)" seconds={author.rawPluginDaySeconds ?? author.pluginDaySeconds ?? author.activeSeconds + author.idleSeconds} />
            <Duration label="Active" seconds={author.activeSeconds} />
            <Duration label="Idle" seconds={author.idleSeconds} />
            <Duration label="Overtime" seconds={author.overtimeActiveSeconds} />
            <Duration
              label="Break"
              seconds={author.breakSeconds}
              className={`break-duration ${breakTone(author.breakSeconds)}`}
              valueClassName={breakClassName(author.breakSeconds)}
            />
            <div className={`duration productivity-duration ${productivityTone(author.productivity)}`}>
              <span>Productivity</span>
              <strong className={productivityClassName(author.productivity)}>{author.productivity.toFixed(2)}%</strong>
            </div>
          </div>

          <div className="dashboard-insights-row">
            <HourlyActivityChart authors={authorHourly} />
            <BreakdownPanel
              key={`${author.rawAuthor}-activity-mix`}
              title="Activity Mix"
              items={activityMix.map((item) => ({
                id: item.type,
                label: formatActivityType(item.type),
                value: item.percent,
                displayValue: `${item.percent}%`,
                color: activityColor(item.type)
              }))}
            />
            <BreakdownPanel
              key={`${author.rawAuthor}-saved-files`}
              title="Saved Files"
              items={savedPrefabs.map((prefab, index) => ({
                id: prefab.path || `${prefab.name}-${index}`,
                label: savedFileLabel(prefab),
                value: prefab.saveCount,
                displayValue: String(prefab.saveCount),
                color: paletteColor(index)
              }))}
            />
            <OvertimeBreakdownPanel
              key={`${author.rawAuthor}-overtime`}
              activityItems={overtimeActivityMix.map((item) => ({
                id: item.type,
                label: formatActivityType(item.type),
                value: item.percent,
                displayValue: `${item.percent}%`,
                color: activityColor(item.type)
              }))}
              savedItems={overtimeSavedPrefabs.map((prefab, index) => ({
                id: prefab.path || `${prefab.name}-${index}`,
                label: savedFileLabel(prefab),
                value: prefab.saveCount,
                displayValue: String(prefab.saveCount),
                color: paletteColor(index)
              }))}
            />
          </div>

          <ReportsTable
            reports={reports}
            total={reportsTotal}
            page={reportsPage}
            pageSize={reportsPageSize}
            sourceFilter={reportSourceFilter}
            sourceOptions={reportSources}
            loading={reportsLoading}
            error={reportsError}
            setPage={setReportsPage}
            setPageSize={setReportsPageSize}
            setSourceFilter={setReportSourceFilter}
          />
        </>
      ) : (
        <p className="empty">No author activity for this period.</p>
      )}
    </section>
  );
}

function SettingsPage({
  summary,
  health,
  currentUser,
  onSaved
}: {
  summary: Summary | null;
  health: Health | null;
  currentUser: SiteUser;
  onSaved: () => void;
}) {
  const profiles = summary?.activitySummary.profiles ?? [];
  const aliases = summary?.activitySummary.authorAliases ?? [];
  const [settingsTab, setSettingsTab] = useState<"authors" | "users">("authors");
  const [drafts, setDrafts] = useState<Record<string, AuthorProfile>>({});
  const [globalInterval, setGlobalInterval] = useState(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
  const [saving, setSaving] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, "saved" | "error" | undefined>>({});
  const [aliasError, setAliasError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<AuthorProfile | null>(null);
  const [deleteProfileTarget, setDeleteProfileTarget] = useState<AuthorProfile | null>(null);
  const [newProfile, setNewProfile] = useState<AuthorProfile>(() => emptyAuthorProfile());
  const [aliasSource, setAliasSource] = useState("");
  const [aliasTarget, setAliasTarget] = useState("");

  useEffect(() => {
    const nextDrafts: Record<string, AuthorProfile> = {};

    for (const profile of profiles) {
      nextDrafts[profile.rawAuthor] = { ...profile };
    }

    setDrafts(nextDrafts);
    setGlobalInterval(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
  }, [summary]);

  useEffect(() => {
    if (!aliasTarget && profiles.length) {
      setAliasTarget(profiles[0].rawAuthor);
    }
  }, [aliasTarget, profiles]);

  async function saveProfile(rawAuthor: string) {
    const profile = drafts[rawAuthor];

    if (!profile) {
      return;
    }

    setSaving(rawAuthor);
    setSaveStatus((items) => ({ ...items, [rawAuthor]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(authorProfilePayload(profile))
      });

      if (!response.ok) {
        throw new Error("Profile save failed");
      }

      setSaveStatus((items) => ({ ...items, [rawAuthor]: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, [rawAuthor]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [rawAuthor]: undefined }));
      }, 2500);
    }
  }

  async function createProfile() {
    const rawAuthor = normalizeAuthorInput(newProfile.rawAuthor);

    if (!rawAuthor) {
      setSaveStatus((items) => ({ ...items, newProfile: "error" }));
      return;
    }

    const profile = {
      ...newProfile,
      rawAuthor,
      displayName: (newProfile.displayName || rawAuthor).trim()
    };

    setSaving("newProfile");
    setSaveStatus((items) => ({ ...items, newProfile: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(authorProfilePayload(profile))
      });

      if (!response.ok) {
        throw new Error("Profile create failed");
      }

      setNewProfile(emptyAuthorProfile());
      setSaveStatus((items) => ({ ...items, newProfile: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, newProfile: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, newProfile: undefined }));
      }, 2500);
    }
  }

  async function saveInterval() {
    setSaving("interval");
    setSaveStatus((items) => ({ ...items, interval: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/intervals`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ defaultSendIntervalSeconds: Number(globalInterval) })
      });

      if (!response.ok) {
        throw new Error("Interval save failed");
      }

      setSaveStatus((items) => ({ ...items, interval: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, interval: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, interval: undefined }));
      }, 2500);
    }
  }

  async function deleteAuthorData(rawAuthor: string) {
    const deleteKey = `delete:${rawAuthor}`;
    setSaving(deleteKey);
    setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/${encodeURIComponent(rawAuthor)}/data`, {
        method: "DELETE"
      });

      if (!response.ok) {
        throw new Error("Author data delete failed");
      }

      setDeleteTarget(null);
      setSaveStatus((items) => ({ ...items, [deleteKey]: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, [deleteKey]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));
      }, 2500);
    }
  }

  async function deleteAuthorProfile(rawAuthor: string) {
    const deleteKey = `delete-profile:${rawAuthor}`;
    setSaving(deleteKey);
    setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/${encodeURIComponent(rawAuthor)}/profile`, {
        method: "DELETE"
      });

      if (!response.ok) {
        throw new Error("Author profile delete failed");
      }

      setDeleteProfileTarget(null);
      setSaveStatus((items) => ({ ...items, [deleteKey]: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, [deleteKey]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));
      }, 2500);
    }
  }

  async function saveAuthorAlias() {
    const sourceRawAuthor = normalizeAuthorInput(aliasSource);
    const targetRawAuthor = normalizeAuthorInput(aliasTarget);

    if (!sourceRawAuthor || !targetRawAuthor || sourceRawAuthor === targetRawAuthor) {
      setSaveStatus((items) => ({ ...items, authorAlias: "error" }));
      return;
    }

    setSaving("authorAlias");
    setAliasError("");
    setSaveStatus((items) => ({ ...items, authorAlias: undefined }));

    try {
      const response = await apiFetch("/api/v1/authors/aliases", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceRawAuthor, targetRawAuthor })
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(String(payload.detail || "Alias save failed"));
      }

      setAliasSource("");
      setSaveStatus((items) => ({ ...items, authorAlias: "saved" }));
      onSaved();
    } catch (error) {
      setAliasError(error instanceof Error ? error.message : "Alias save failed");
      setSaveStatus((items) => ({ ...items, authorAlias: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, authorAlias: undefined }));
      }, 2500);
    }
  }

  async function deleteAuthorAlias(sourceRawAuthor: string) {
    const deleteKey = `alias-delete:${sourceRawAuthor}`;
    setSaving(deleteKey);
    setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/aliases/${encodeURIComponent(sourceRawAuthor)}`, { method: "DELETE" });

      if (!response.ok) {
        throw new Error("Alias delete failed");
      }

      setSaveStatus((items) => ({ ...items, [deleteKey]: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, [deleteKey]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));
      }, 2500);
    }
  }

  function isProfileDirty(profile: AuthorProfile) {
    const draft = drafts[profile.rawAuthor] ?? profile;

    return (
      (draft.displayName ?? "") !== (profile.displayName ?? "") ||
      (draft.team ?? "") !== (profile.team ?? "") ||
      (draft.telegramUsername ?? "") !== (profile.telegramUsername ?? "") ||
      (draft.discordUserId ?? "") !== (profile.discordUserId ?? "") ||
      (draft.discordUsername ?? "") !== (profile.discordUsername ?? "") ||
      (draft.authorColor ?? "") !== (profile.authorColor ?? "") ||
      (draft.pluginEnabled ?? true) !== (profile.pluginEnabled ?? true)
    );
  }

  return (
    <section className="page-section settings-layout">
      <div className="settings-tabs">
        <button className={settingsTab === "authors" ? "active" : ""} onClick={() => setSettingsTab("authors")}>Author Profiles</button>
        <button className={settingsTab === "users" ? "active" : ""} onClick={() => setSettingsTab("users")}>Site Users</button>
      </div>
      {settingsTab === "authors" ? (
        <>
      <div className="panel">
        <h2>System Status</h2>
        <span className={health?.ok ? "status-pill online" : "status-pill"}>{health?.ok ? "Backend online" : "Backend offline"}</span>
      </div>

      <div className="settings-card-row">
        <div className="panel">
          <h2>Send Interval</h2>
          <div className="settings-row">
            <label>
              Global interval, sec
              <input value={globalInterval} onChange={(event) => setGlobalInterval(event.target.value)} type="number" min="30" />
            </label>
            <button className={settingsSaveButtonClassName(saveStatus.interval)} onClick={() => void saveInterval()} disabled={saving === "interval"}>
              {settingsSaveButtonLabel("interval", saving, saveStatus)}
            </button>
          </div>
        </div>

        <div className="panel">
          <h2>Author Redirects</h2>
          <p className="settings-caption">
            Redirect a trash raw author from plugin reports to the correct author profile. The source profile is removed and future reports aggregate into the target profile.
          </p>
          <div className="profile-create-card author-alias-card">
            <label>
              Source raw author
              <input
                value={aliasSource}
                onChange={(event) => setAliasSource(event.target.value)}
                list="author-alias-source-list"
                placeholder="Unknown User"
              />
              <datalist id="author-alias-source-list">
                {profiles.map((profile) => (
                  <option value={profile.rawAuthor} key={profile.rawAuthor} />
                ))}
              </datalist>
            </label>
            <label>
              Target profile
              <select value={aliasTarget} onChange={(event) => setAliasTarget(event.target.value)}>
                {profiles.map((profile) => (
                  <option value={profile.rawAuthor} key={profile.rawAuthor}>{profile.displayName || profile.rawAuthor}</option>
                ))}
              </select>
            </label>
            <button
              className={settingsSaveButtonClassName(saveStatus.authorAlias)}
              onClick={() => void saveAuthorAlias()}
              disabled={saving === "authorAlias" || !aliasSource.trim() || !aliasTarget.trim()}
            >
              {saving === "authorAlias" ? "Assigning..." : saveStatus.authorAlias === "saved" ? "Assigned" : saveStatus.authorAlias === "error" ? "Failed" : "Assign"}
            </button>
          </div>
          {aliasError ? <p className="notice error">{aliasError}</p> : null}
          <div className="alias-list">
            {aliases.length ? (
              aliases.map((alias) => {
                const target = profiles.find((profile) => profile.rawAuthor === alias.targetRawAuthor);
                const deleteKey = `alias-delete:${alias.sourceRawAuthor}`;
                return (
                  <div className="alias-row" key={alias.sourceRawAuthor}>
                    <span><strong>{alias.sourceRawAuthor}</strong> redirects to <strong>{target?.displayName || alias.targetRawAuthor}</strong></span>
                    <button
                      className={`${settingsSaveButtonClassName(saveStatus[deleteKey], true)} danger-button`}
                      onClick={() => void deleteAuthorAlias(alias.sourceRawAuthor)}
                      disabled={saving === deleteKey}
                    >
                      {saving === deleteKey ? "Deleting..." : saveStatus[deleteKey] === "error" ? "Failed" : "Delete"}
                    </button>
                  </div>
                );
              })
            ) : (
              <p className="empty">No redirects yet.</p>
            )}
          </div>
        </div>
      </div>

      <div className="panel">
        <h2>Author Profiles</h2>
        <p className="settings-caption">
          Telegram and Discord mappings link chat and meeting events to the author.
          Raw Author must exactly match the value sent by activity logger plugins.
        </p>
        <div className="profile-create-card">
          <label>
            Raw Author
            <input
              value={newProfile.rawAuthor}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, rawAuthor: event.target.value }))}
              placeholder="Git user.name"
            />
          </label>
          <label>
            Display Name
            <input
              value={newProfile.displayName}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, displayName: event.target.value }))}
              placeholder="Shown on dashboard"
            />
          </label>
          <label>
            Team
            <input
              value={newProfile.team ?? ""}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, team: event.target.value }))}
              placeholder="Team"
            />
          </label>
          <label>
            Telegram
            <input
              value={newProfile.telegramUsername ?? ""}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, telegramUsername: event.target.value }))}
              placeholder="@username"
            />
          </label>
          <label>
            Discord ID
            <input
              value={newProfile.discordUserId ?? ""}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, discordUserId: event.target.value }))}
              placeholder="User ID"
            />
          </label>
          <label>
            Discord Name
            <input
              value={newProfile.discordUsername ?? ""}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, discordUsername: event.target.value }))}
              placeholder="username"
            />
          </label>
          <label>
            Color
            <input
              type="color"
              value={newProfile.authorColor ?? "#13a37b"}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, authorColor: event.target.value }))}
            />
          </label>
          <label>
            Plugin
            <span className="checkbox-cell">
              <input
                type="checkbox"
                checked={newProfile.pluginEnabled ?? true}
                onChange={(event) => setNewProfile((profile) => ({ ...profile, pluginEnabled: event.target.checked }))}
              />
              Enabled
            </span>
          </label>
          <button
            className={settingsSaveButtonClassName(saveStatus.newProfile)}
            onClick={() => void createProfile()}
            disabled={saving === "newProfile" || !newProfile.rawAuthor.trim()}
          >
            {saving === "newProfile" ? "Creating..." : saveStatus.newProfile === "saved" ? "Created" : saveStatus.newProfile === "error" ? "Failed" : "Add profile"}
          </button>
        </div>
        <div className="profile-table">
          <div className="profile-table-head">
            <span>Raw Author</span>
            <span>Display Name</span>
            <span>Team</span>
            <span>Telegram</span>
            <span>Discord ID</span>
            <span>Discord Name</span>
            <span>Timezone</span>
            <span>Color</span>
            <span>Plugin</span>
            <span>Actions</span>
          </div>
          {profiles.map((profile) => {
            const draft = drafts[profile.rawAuthor] ?? profile;
            const profileDirty = isProfileDirty(profile);
            const deleteKey = `delete:${profile.rawAuthor}`;
            const deleteProfileKey = `delete-profile:${profile.rawAuthor}`;
            return (
              <div className="profile-row" key={profile.rawAuthor}>
                <span className="profile-author-cell" title={profile.authorEmail || profile.rawAuthor}>
                  <strong>{profile.rawAuthor}</strong>
                  <small>{profile.authorEmail || "-"}</small>
                </span>
                <input
                  value={draft.displayName}
                  onChange={(event) => setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, displayName: event.target.value } }))}
                />
                <input
                  value={draft.team ?? ""}
                  onChange={(event) => setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, team: event.target.value } }))}
                />
                <input
                  value={draft.telegramUsername ?? ""}
                  onChange={(event) =>
                    setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, telegramUsername: event.target.value } }))
                  }
                  placeholder="@username"
                />
                <input
                  value={draft.discordUserId ?? ""}
                  onChange={(event) =>
                    setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, discordUserId: event.target.value } }))
                  }
                  placeholder="User ID"
                />
                <input
                  value={draft.discordUsername ?? ""}
                  onChange={(event) =>
                    setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, discordUsername: event.target.value } }))
                  }
                  placeholder="username"
                />
                <span className="profile-readonly-cell" title={formatProfileTimeZoneTitle(profile)}>{formatProfileTimeZoneLabel(profile)}</span>
                <input
                  type="color"
                  value={draft.authorColor ?? "#13a37b"}
                  onChange={(event) => setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, authorColor: event.target.value } }))}
                />
                <label className="checkbox-cell">
                  <input
                    type="checkbox"
                    checked={draft.pluginEnabled ?? true}
                    onChange={(event) =>
                      setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, pluginEnabled: event.target.checked } }))
                    }
                  />
                  Enabled
                </label>
                <div className="profile-actions">
                  <button
                    className={settingsSaveButtonClassName(saveStatus[profile.rawAuthor], true)}
                    onClick={() => void saveProfile(profile.rawAuthor)}
                    disabled={saving === profile.rawAuthor || !profileDirty}
                  >
                    {settingsSaveButtonLabel(profile.rawAuthor, saving, saveStatus)}
                  </button>
                  <button
                    className={`${settingsSaveButtonClassName(saveStatus[deleteKey], true)} danger-button`}
                    onClick={() => setDeleteTarget(profile)}
                    disabled={saving === deleteKey}
                  >
                    {saving === deleteKey ? "Deleting..." : saveStatus[deleteKey] === "error" ? "Failed" : "Delete data"}
                  </button>
                  <button
                    className={`${settingsSaveButtonClassName(saveStatus[deleteProfileKey], true)} danger-button`}
                    onClick={() => setDeleteProfileTarget(profile)}
                    disabled={saving === deleteProfileKey}
                  >
                    {saving === deleteProfileKey ? "Deleting..." : saveStatus[deleteProfileKey] === "error" ? "Failed" : "Delete profile"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {deleteTarget ? (
        <AuthorDeleteConfirm
          profile={deleteTarget}
          saving={saving === `delete:${deleteTarget.rawAuthor}`}
          onCancel={() => setDeleteTarget(null)}
          onDelete={() => void deleteAuthorData(deleteTarget.rawAuthor)}
        />
      ) : null}
      {deleteProfileTarget ? (
        <AuthorProfileDeleteConfirm
          profile={deleteProfileTarget}
          saving={saving === `delete-profile:${deleteProfileTarget.rawAuthor}`}
          onCancel={() => setDeleteProfileTarget(null)}
          onDelete={() => void deleteAuthorProfile(deleteProfileTarget.rawAuthor)}
        />
      ) : null}
        </>
      ) : (
        <SiteUsersPanel currentUser={currentUser} />
      )}
    </section>
  );
}

function SiteUsersPanel({ currentUser }: { currentUser: SiteUser }) {
  const canManageUsers = currentUser.role === "admin";
  const [users, setUsers] = useState<SiteUser[]>([]);
  const [drafts, setDrafts] = useState<Record<string, SiteUser>>({});
  const [newUser, setNewUser] = useState<SiteUser & { password: string }>({
    email: "",
    displayName: "",
    role: "viewer",
    active: true,
    password: ""
  });
  const [saving, setSaving] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, "saved" | "error" | undefined>>({});

  async function loadUsers() {
    if (!canManageUsers) {
      return;
    }

    const response = await apiFetch("/api/v1/site-users");

    if (response.ok) {
      const payload = await response.json();
      const nextUsers = payload.users ?? [];
      setUsers(nextUsers);
      setDrafts(Object.fromEntries(nextUsers.map((user: SiteUser) => [user.email, user])));
    }
  }

  useEffect(() => {
    void loadUsers();
  }, [canManageUsers]);

  async function saveUser(email: string, password?: string) {
    const draft = drafts[email];

    if (!draft) {
      return;
    }

    await persistUser(email, { ...draft, password });
  }

  async function createUser() {
    await persistUser("newUser", newUser);
  }

  async function persistUser(key: string, user: SiteUser & { password?: string }) {
    setSaving(key);
    setStatus((items) => ({ ...items, [key]: undefined }));

    try {
      const response = await apiFetch("/api/v1/site-users", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: user.email,
          displayName: user.displayName,
          role: user.role,
          active: user.active,
          password: user.password || undefined
        })
      });

      if (!response.ok) {
        throw new Error("User save failed");
      }

      if (key === "newUser") {
        setNewUser({ email: "", displayName: "", role: "viewer", active: true, password: "" });
      }

      setStatus((items) => ({ ...items, [key]: "saved" }));
      await loadUsers();
    } catch {
      setStatus((items) => ({ ...items, [key]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setStatus((items) => ({ ...items, [key]: undefined }));
      }, 2500);
    }
  }

  async function deleteUser(email: string) {
    setSaving(`delete:${email}`);
    setStatus((items) => ({ ...items, [`delete:${email}`]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/site-users/${encodeURIComponent(email)}`, { method: "DELETE" });

      if (!response.ok) {
        throw new Error("User delete failed");
      }

      setStatus((items) => ({ ...items, [`delete:${email}`]: "saved" }));
      await loadUsers();
    } catch {
      setStatus((items) => ({ ...items, [`delete:${email}`]: "error" }));
    } finally {
      setSaving(null);
    }
  }

  if (!canManageUsers) {
    return (
      <div className="panel">
        <h2>Site Users</h2>
        <p className="settings-caption">Only admins can create users, reset passwords, and change access rights.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>Site Users</h2>
      <p className="settings-caption">Create dashboard logins, issue temporary passwords, and choose what each person can do on the site.</p>
      <div className="site-user-create-card">
        <label>
          Email
          <input value={newUser.email} onChange={(event) => setNewUser((user) => ({ ...user, email: event.target.value }))} />
        </label>
        <label>
          Display Name
          <input value={newUser.displayName} onChange={(event) => setNewUser((user) => ({ ...user, displayName: event.target.value }))} />
        </label>
        <label>
          Password
          <input
            value={newUser.password}
            onChange={(event) => setNewUser((user) => ({ ...user, password: event.target.value }))}
            type="password"
            minLength={8}
          />
        </label>
        <label>
          Role
          <select value={newUser.role} onChange={(event) => setNewUser((user) => ({ ...user, role: event.target.value as SiteUserRole }))}>
            <option value="admin">Admin</option>
            <option value="editor">Editor</option>
            <option value="viewer">Viewer</option>
          </select>
        </label>
        <button
          className={settingsSaveButtonClassName(status.newUser)}
          onClick={() => void createUser()}
          disabled={saving === "newUser" || !newUser.email.trim() || newUser.password.length < 8}
        >
          {saving === "newUser" ? "Creating..." : status.newUser === "saved" ? "Created" : status.newUser === "error" ? "Failed" : "Add user"}
        </button>
      </div>
      <div className="site-users-table">
        <div className="site-users-head">
          <span>Email</span>
          <span>Name</span>
          <span>Role</span>
          <span>Status</span>
          <span>New Password</span>
          <span>Actions</span>
        </div>
        {users.map((user) => {
          const draft = drafts[user.email] ?? user;
          const deleteKey = `delete:${user.email}`;
          return (
            <div className="site-user-row" key={user.email}>
              <strong>{user.email}</strong>
              <input
                value={draft.displayName}
                onChange={(event) => setDrafts((items) => ({ ...items, [user.email]: { ...draft, displayName: event.target.value } }))}
              />
              <select
                value={draft.role}
                onChange={(event) => setDrafts((items) => ({ ...items, [user.email]: { ...draft, role: event.target.value as SiteUserRole } }))}
              >
                <option value="admin">Admin</option>
                <option value="editor">Editor</option>
                <option value="viewer">Viewer</option>
              </select>
              <label className="checkbox-cell">
                <input
                  type="checkbox"
                  checked={draft.active}
                  onChange={(event) => setDrafts((items) => ({ ...items, [user.email]: { ...draft, active: event.target.checked } }))}
                />
                Active
              </label>
              <input
                type="password"
                placeholder="Leave unchanged"
                onChange={(event) => setDrafts((items) => ({ ...items, [user.email]: { ...draft, password: event.target.value } as SiteUser }))}
              />
              <div className="profile-actions">
                <button
                  className={settingsSaveButtonClassName(status[user.email], true)}
                  onClick={() => void saveUser(user.email, (draft as SiteUser & { password?: string }).password)}
                  disabled={saving === user.email}
                >
                  {settingsSaveButtonLabel(user.email, saving, status)}
                </button>
                <button
                  className={`${settingsSaveButtonClassName(status[deleteKey], true)} danger-button`}
                  onClick={() => void deleteUser(user.email)}
                  disabled={saving === deleteKey || user.email === currentUser.email}
                >
                  {saving === deleteKey ? "Deleting..." : "Delete"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AuthorDeleteConfirm({
  profile,
  saving,
  onCancel,
  onDelete
}: {
  profile: AuthorProfile;
  saving: boolean;
  onCancel: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="calendar-modal">
        <h2>Delete all data for {profile.displayName}</h2>
        <p className="calendar-helper">
          This will remove reports, raw activity events, Telegram day/break data, alerts, and activity statistics for this
          author. The author profile, display name, Telegram username, color, and plugin settings will stay unchanged. This action cannot be undone.
        </p>
        <div className="modal-actions">
          <button className="primary-outline-button" onClick={onCancel} disabled={saving}>Cancel</button>
          <button className="primary-button danger-solid-button" onClick={onDelete} disabled={saving}>
            {saving ? "Deleting..." : "Delete all author data"}
          </button>
        </div>
      </div>
    </div>
  );
}

function AuthorProfileDeleteConfirm({
  profile,
  saving,
  onCancel,
  onDelete
}: {
  profile: AuthorProfile;
  saving: boolean;
  onCancel: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="calendar-modal">
        <h2>Delete profile for {profile.displayName}</h2>
        <p className="calendar-helper">
          This will remove the author profile, Telegram mapping, plugin settings, reports, raw activity events, Telegram day/break data,
          calendar marks, alerts, and activity statistics for this author. This action cannot be undone.
        </p>
        <div className="modal-actions">
          <button className="primary-outline-button" onClick={onCancel} disabled={saving}>Cancel</button>
          <button className="primary-button danger-solid-button" onClick={onDelete} disabled={saving}>
            {saving ? "Deleting..." : "Delete profile and all data"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ReportsTable({
  reports,
  total,
  page,
  pageSize,
  sourceFilter,
  sourceOptions,
  loading,
  error,
  setPage,
  setPageSize,
  setSourceFilter
}: {
  reports: Report[];
  total: number;
  page: number;
  pageSize: number;
  sourceFilter: string;
  sourceOptions: string[];
  loading: boolean;
  error: string | null;
  setPage: (value: number | ((current: number) => number)) => void;
  setPageSize: (value: number) => void;
  setSourceFilter: (value: string) => void;
}) {
  const pageSizeOptions = [10, 25, 50];
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * pageSize;
  const pageEnd = Math.min(pageStart + reports.length, total);

  useEffect(() => {
    if (!sourceFilter) {
      return;
    }

    const available = new Set(sourceOptions);

    if (!available.has(sourceFilter)) {
      setSourceFilter("");
    }
  }, [sourceOptions, sourceFilter, setSourceFilter]);

  return (
    <section className="panel table-panel">
      <div className="table-panel-header">
        <h2>Plugin Reports</h2>
        <label className="table-panel-filter">
          <span>Source</span>
          <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
            <option value="">All sources</option>
            {sourceOptions.map((key) => (
              <option key={key || "__none__"} value={key}>
                {key ? formatSource(key) : "Unknown"}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="table" style={{ "--reports-page-size": pageSize } as React.CSSProperties}>
        <div className="table-head">
          <span>Source</span>
          <span>Author</span>
          <span>Date</span>
          <span>Active</span>
          <span>Idle</span>
          <span>Overtime</span>
          <span>Recorded</span>
          <span>Type</span>
          <span>Timezone</span>
        </div>
        <div className="table-body">
          {loading ? <div className="table-state">Loading reports...</div> : null}
          {error ? <div className="table-state">{error}</div> : null}
          {!loading && !error && reports.length === 0 ? <div className="table-state">No reports for this period.</div> : null}
          {!loading && !error ? reports.map((report, index) => (
            <div className="table-row" key={`${report.recordedAt ?? "report"}-${index}`}>
              <span className="source-cell">{sourceIcon(report.source)}{formatSource(report.source)}</span>
              <span>{report.displayName ?? report.author ?? "Unknown User"}</span>
              <span>{report.date ?? "-"}</span>
              <span>{formatReportActive(report)}</span>
              <span>{formatReportIdle(report)}</span>
              <span>{formatReportOvertime(report.overtimeActiveDeltaSeconds ?? 0)}</span>
              <span>{formatAuthorTime(report)}</span>
              <span className={reportTypeBadgeClassName(report.reportType)}>{formatReportType(report)}</span>
              <span>{formatTimeZoneLabel(report) ?? "-"}</span>
            </div>
          )) : null}
        </div>
      </div>
      <div className="table-pagination">
        <span>
          Rows {total ? pageStart + 1 : 0}-{pageEnd} of {total}
        </span>
        <label>
          Rows per page
          <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
            {pageSizeOptions.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </label>
        <div className="pagination-buttons">
          <button className="primary-outline-button" onClick={() => setPage(1)} disabled={currentPage === 1}>
            First
          </button>
          <button className="primary-outline-button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={currentPage === 1}>
            Prev
          </button>
          <span>{currentPage} / {totalPages}</span>
          <button className="primary-outline-button" onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={currentPage === totalPages}>
            Next
          </button>
          <button className="primary-outline-button" onClick={() => setPage(totalPages)} disabled={currentPage === totalPages}>
            Last
          </button>
        </div>
      </div>
    </section>
  );
}

function settingsSaveButtonLabel(key: string, saving: string | null, statuses: Record<string, "saved" | "error" | undefined>) {
  if (saving === key) {
    return "Saving...";
  }

  if (statuses[key] === "saved") {
    return "Saved";
  }

  if (statuses[key] === "error") {
    return "Failed";
  }

  return "Save";
}

function dashboardRefreshIntervalMs(summary: Summary | null) {
  const seconds = summary?.intervalSettings.defaultSendIntervalSeconds ?? REFRESH_INTERVAL_MS / 1000;
  return Math.max(1000, seconds * 1000);
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

function settingsSaveButtonClassName(status: "saved" | "error" | undefined, outline = false) {
  const baseClassName = outline ? "primary-outline-button" : "primary-button";

  if (status === "saved") {
    return `${baseClassName} save-success`;
  }

  if (status === "error") {
    return `${baseClassName} save-error`;
  }

  return baseClassName;
}

function emptyAuthorProfile(): AuthorProfile {
  return {
    rawAuthor: "",
    displayName: "",
    team: "",
    telegramUsername: "",
    discordUserId: "",
    discordUsername: "",
    pluginEnabled: true,
    authorColor: "#13a37b"
  };
}

function authorProfilePayload(profile: AuthorProfile) {
  return {
    rawAuthor: profile.rawAuthor,
    displayName: profile.displayName,
    team: profile.team ?? "",
    telegramUsername: profile.telegramUsername ?? "",
    discordUserId: profile.discordUserId ?? "",
    discordUsername: profile.discordUsername ?? "",
    pluginEnabled: profile.pluginEnabled ?? true,
    authorColor: profile.authorColor ?? "#13a37b"
  };
}

function normalizeAuthorInput(value: string) {
  return value.trim().normalize("NFC");
}

type BreakdownPanelItem = {
  id: string;
  label: string;
  value: number;
  displayValue: string;
  color: string;
};

function BreakdownPanel({ title, items }: { title: string; items: BreakdownPanelItem[] }) {
  const total = items.reduce((sum, item) => sum + Math.max(0, item.value), 0);
  const barStyle = {
    "--bar-gradient": segmentedBarGradient(items, total)
  } as React.CSSProperties;

  return (
    <div className="panel breakdown-panel">
      <div className="breakdown-panel-copy">
        <h2>{title}</h2>
        <div className="list breakdown-scroll-list breakdown-scroll-list--compact-rows">
          {items.length ? (
            items.map((item) => (
              <div className="row" key={item.id}>
                <span><i className="row-color" style={{ background: item.color }} />{item.label}</span>
                <strong>{item.displayValue}</strong>
              </div>
            ))
          ) : (
            <p className="empty">No data yet.</p>
          )}
        </div>
      </div>
      <div className="breakdown-bar-row">
        <div className="breakdown-bar" style={barStyle} aria-hidden="true" />
        <strong>{total ? totalDisplayValue(items) : "-"}</strong>
      </div>
    </div>
  );
}

function OvertimeBreakdownPanel({
  activityItems,
  savedItems
}: {
  activityItems: BreakdownPanelItem[];
  savedItems: BreakdownPanelItem[];
}) {
  return (
    <div className="panel breakdown-panel overtime-breakdown-panel">
      <h2>Overtime</h2>
      <MiniBreakdownList title="Activity Mix" items={activityItems} emptyMessage="No overtime activity yet." />
      <MiniBreakdownList title="Saved Files" items={savedItems} emptyMessage="No overtime saves yet." />
    </div>
  );
}

function MiniBreakdownList({
  title,
  items,
  emptyMessage
}: {
  title: string;
  items: BreakdownPanelItem[];
  emptyMessage: string;
}) {
  return (
    <div className="mini-breakdown-list">
      <h3>{title}</h3>
      <div className="list breakdown-scroll-list breakdown-scroll-list--standard-rows">
        {items.length ? (
          items.map((item) => (
            <div className="row" key={item.id}>
              <span><i className="row-color" style={{ background: item.color }} />{item.label}</span>
              <strong>{item.displayValue}</strong>
            </div>
          ))
        ) : (
          <p className="empty">{emptyMessage}</p>
        )}
      </div>
    </div>
  );
}

function PanelList({ title, items }: { title: string; items: Array<[string, string]> }) {
  return (
    <div className="panel">
      <h2>{title}</h2>
      <div className="list">
        {items.length ? (
          items.map(([label, value]) => (
            <div className="row" key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))
        ) : (
          <p className="empty">No data yet.</p>
        )}
      </div>
    </div>
  );
}

function segmentedBarGradient(items: BreakdownPanelItem[], total: number) {
  if (!items.length || total <= 0) {
    return "#edf2f7 0% 100%";
  }

  let cursor = 0;
  const segments = items.map((item) => {
    const start = cursor;
    const width = (Math.max(0, item.value) / total) * 100;
    cursor += width;
    return `${item.color} ${start}% ${cursor}%`;
  });

  return `linear-gradient(to right, ${segments.join(", ")})`;
}

function totalDisplayValue(items: BreakdownPanelItem[]) {
  const percentItems = items.every((item) => item.displayValue.endsWith("%"));

  if (percentItems) {
    return "100%";
  }

  return String(items.reduce((sum, item) => sum + Math.max(0, item.value), 0));
}

function activityColor(type: string) {
  const normalized = type.toLowerCase();

  if (normalized === "select") {
    return "#5b4dff";
  }

  if (normalized === "undo_redo") {
    return "#f59e0b";
  }

  if (normalized === "prefab_saved") {
    return "#13a37b";
  }

  if (normalized === "play_mode") {
    return "#0ea5e9";
  }

  if (normalized === "scene_saved") {
    return "#ef4444";
  }

  return paletteColor(normalized.length);
}

function paletteColor(index: number) {
  const colors = ["#5b4dff", "#13a37b", "#f59e0b", "#0ea5e9", "#a855f7", "#ef4444", "#14b8a6"];
  return colors[index % colors.length];
}

function savedFileLabel(prefab: SavedPrefab) {
  const fileName = prefab.name || prefab.path;

  if (!prefab.projectId || fileName === prefab.projectId) {
    return fileName;
  }

  return `${fileName} · ${prefab.projectId}`;
}

function DateRangePicker({ value, onChange }: { value: DateRange; onChange: (range: DateRange) => void }) {
  function updateDateRange(next: Pick<DateRange, "startDate" | "endDate">) {
    onChange({ ...next, preset: "custom" });
  }

  return (
    <div className="date-range-group">
      <div className="date-presets" aria-label="Date presets">
        <button className={`live-preset-button ${value.preset === "live" ? "active" : ""}`.trim()} onClick={() => onChange(todayRange())}>
          <span className="live-preset-dot" aria-hidden="true" />
          Live
        </button>
        <button className={value.preset === "yesterday" ? "active" : undefined} onClick={() => onChange(yesterdayRange())}>Yesterday</button>
      </div>
      <div className="date-range-control">
        <input type="date" value={value.startDate} onChange={(event) => updateDateRange({ ...value, startDate: event.target.value })} />
        <span>to</span>
        <input type="date" value={value.endDate} onChange={(event) => updateDateRange({ ...value, endDate: event.target.value })} />
      </div>
    </div>
  );
}

function Duration({ label, seconds, className, valueClassName }: { label: string; seconds: number; className?: string; valueClassName?: string }) {
  return (
    <div className={`duration${className ? ` ${className}` : ""}`}>
      <span>{label}</span>
      <strong className={valueClassName}>{formatDuration(seconds)}</strong>
    </div>
  );
}

function formatDuration(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function formatReportMinutes(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainingSeconds = rounded % 60;

  if (hours > 0) {
    if (remainingSeconds > 0) {
      return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(remainingSeconds).padStart(2, "0")}s`;
    }

    return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  }

  if (minutes > 0) {
    if (remainingSeconds > 0) {
      return `${minutes}m ${String(remainingSeconds).padStart(2, "0")}s`;
    }

    return `${minutes}m`;
  }

  return `${remainingSeconds}s`;
}

function formatReportOvertime(seconds: number) {
  return seconds > 0 ? formatReportMinutes(seconds) : "-";
}

function formatReportActive(report: Report) {
  return isNonActivityReport(report) ? "-" : formatReportMinutes(report.activeDeltaSeconds ?? 0);
}

function formatReportIdle(report: Report) {
  return isNonActivityReport(report) ? "-" : formatReportMinutes(report.idleDeltaSeconds ?? 0);
}

function isNonActivityReport(report: Report) {
  return report.source === "telegram" || report.reportType === "telegram" || report.source === "discord" || report.reportType === "meeting";
}

function formatDurationDelta(seconds: number) {
  const prefix = seconds >= 0 ? "+" : "-";
  return `${prefix}${formatDuration(Math.abs(seconds))}`;
}

function formatDelta(value: number) {
  const prefix = value >= 0 ? "+" : "";
  return `${prefix}${value.toFixed(1)}`;
}

function monthIndexes() {
  return Array.from({ length: 12 }, (_, index) => index);
}

function toCalendarDate(year: number, month: number, day: number) {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function calendarDayClassName(selected: boolean, isToday: boolean, isPast: boolean) {
  const classNames = ["calendar-day"];

  if (selected) {
    classNames.push("selected");
  }

  if (isToday) {
    classNames.push("today");
  }

  if (isPast) {
    classNames.push("locked");
  }

  return classNames.join(" ");
}

function uniqueDates(dates: string[]) {
  return Array.from(new Set(dates)).sort();
}

function dateRangeList(startDate: string, endDate: string) {
  const start = new Date(startDate);
  const end = new Date(endDate);
  const cursor = new Date(start <= end ? start : end);
  const last = new Date(start <= end ? end : start);
  const dates = [];

  while (cursor <= last) {
    dates.push(toDateInputValue(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }

  return dates;
}

function formatMinutes(seconds: number) {
  return `${Math.max(0, Math.round(seconds / 60))}m`;
}

function productivityClassName(productivity: number) {
  if (productivity > 100) {
    return "metric-value overdrive";
  }

  if (productivity > 80) {
    return "metric-value good";
  }

  if (productivity >= 50) {
    return "metric-value warning";
  }

  return "metric-value bad";
}

function productivityTone(productivity: number) {
  if (productivity > 100) {
    return "overdrive";
  }

  if (productivity > 80) {
    return "good";
  }

  if (productivity >= 50) {
    return "warning";
  }

  return "bad";
}

function authorCardProductivityTone(author: AuthorRow) {
  if (author.status === "stale" && !hasAuthorActivity(author)) {
    return "neutral";
  }

  return productivityTone(author.productivity);
}

function hasAuthorActivity(author: AuthorRow) {
  return [
    author.activeSeconds,
    author.idleSeconds,
    author.meetingSeconds,
    author.breakSeconds,
    author.overtimeActiveSeconds,
    author.telegramDaySeconds,
    author.rawPluginDaySeconds ?? author.pluginDaySeconds
  ].some((value) => Number(value) > 0);
}

function breakClassName(seconds: number) {
  return `metric-value ${breakTone(seconds)}`;
}

function breakTone(seconds: number) {
  return seconds > 61 * 60 ? "bad" : "good";
}

function formatSource(source?: string) {
  if (source === "ual") {
    return "Unity";
  }

  if (source === "bal") {
    return "Blender";
  }

  if (source === "fch") {
    return "FigmaWeb";
  }

  if (source === "fig") {
    return "FigmaApp";
  }

  if (source === "vsc") {
    return "VS Code";
  }

  if (source === "cur") {
    return "Cursor";
  }

  if (source === "telegram") {
    return "Telegram";
  }

  if (source === "discord") {
    return "Discord";
  }

  return source ?? "-";
}

function sourceIcon(source?: string) {
  if (source === "ual") {
    return <Box size={16} />;
  }

  if (source === "bal") {
    return <BlenderIcon />;
  }

  if (source === "fch" || source === "fig") {
    return <FigmaIcon />;
  }

  if (source === "vsc") {
    return <VSCodeIcon />;
  }

  if (source === "cur") {
    return <CursorIcon />;
  }

  if (source === "telegram") {
    return <TelegramIcon />;
  }

  if (source === "discord") {
    return <DiscordIcon />;
  }

  return <Activity size={16} />;
}

function BlenderIcon() {
  return (
    <svg className="source-icon blender-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path className="blender-icon-mark" d="M9.1 3.1c-.5-.5-.5-1.2 0-1.7.5-.5 1.2-.5 1.7 0l4.6 4.4h3.5c.7 0 1.2.5 1.2 1.2s-.5 1.2-1.2 1.2h-1.1l1.9 1.8c1 .9 1.5 2.1 1.5 3.5 0 4-4.3 7.2-9.5 7.2-4.8 0-8.7-2.6-8.7-5.9 0-2.7 2.6-5 6.1-5.7H2.5c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2h8.3L9.1 5.2H5.6c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2h3.1l.4.3Z" />
      <path className="blender-icon-eye" d="M12.1 11.2c2.5 0 4.5 1.4 4.5 3.1s-2 3.1-4.5 3.1-4.5-1.4-4.5-3.1 2-3.1 4.5-3.1Z" />
      <path className="blender-icon-pupil" d="M12.1 12.8c1.2 0 2.1.7 2.1 1.5s-.9 1.5-2.1 1.5-2.1-.7-2.1-1.5.9-1.5 2.1-1.5Z" />
    </svg>
  );
}

function FigmaIcon() {
  return (
    <svg className="source-icon figma-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path className="figma-red" d="M12 2H8.4a3.4 3.4 0 1 0 0 6.8H12V2Z" />
      <path className="figma-purple" d="M12 8.8H8.4a3.4 3.4 0 1 0 0 6.8H12V8.8Z" />
      <path className="figma-blue" d="M15.6 8.8H12v6.8h3.6a3.4 3.4 0 1 0 0-6.8Z" />
      <path className="figma-green" d="M8.4 15.6H12V19a3.4 3.4 0 1 1-3.6-3.4Z" />
      <path className="figma-orange" d="M12 2h3.6a3.4 3.4 0 1 1 0 6.8H12V2Z" />
    </svg>
  );
}

function VSCodeIcon() {
  return (
    <svg className="source-icon vscode-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M17.9 2.4 7.6 11.8 3.2 8.4 1.8 9.2v5.6l1.4.8 4.4-3.4 10.3 9.4 4.3-1.7V4.1l-4.3-1.7Zm-.4 5.7v7.8l-5.8-3.9 5.8-3.9Z" />
    </svg>
  );
}

function CursorIcon() {
  return <img className="source-icon cursor-icon" src={cursorIconUrl} alt="" aria-hidden="true" />;
}

function TelegramIcon() {
  return (
    <svg className="source-icon telegram-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M21.8 4.1 18.6 20c-.2 1.1-.9 1.4-1.8.9l-5-3.7-2.4 2.3c-.3.3-.5.5-1 .5l.4-5.1 9.3-8.4c.4-.4-.1-.6-.6-.2L6 13.5 1.1 12c-1.1-.3-1.1-1.1.2-1.6L20.4 3c.9-.3 1.7.2 1.4 1.1Z" />
    </svg>
  );
}

function DiscordIcon() {
  return (
    <svg className="source-icon discord-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M19.5 5.4A16 16 0 0 0 15.5 4l-.2.4c1.5.4 2.7 1 3.8 1.8a12.9 12.9 0 0 0-4.7-1.5 13.8 13.8 0 0 0-4.8 0 12.9 12.9 0 0 0-4.7 1.5A11.8 11.8 0 0 1 8.7 4.4L8.5 4a16 16 0 0 0-4 1.4C2 9.1 1.4 12.7 1.7 16.2A16.1 16.1 0 0 0 6.6 18.7l.6-.8a10.4 10.4 0 0 1-1.6-.8l.4-.3c3.1 1.4 6.5 1.4 9.6 0l.4.3c-.5.3-1 .6-1.6.8l.6.8a16.1 16.1 0 0 0 4.9-2.5c.4-4-.7-7.6-2.4-10.8ZM8.5 14.2c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.7.8 1.6 1.8c0 1-.7 1.8-1.6 1.8Zm7 0c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.7.8 1.6 1.8c0 1-.7 1.8-1.6 1.8Z" />
    </svg>
  );
}

function formatReportType(report: Report) {
  if (report.reportType === "telegram") {
    return formatTelegramEvent(report.telegramEventType ?? report.activityType, report.telegramStatus);
  }

  if (report.reportType === "meeting") {
    return formatDiscordEvent(report.discordEventType ?? report.activityType);
  }

  if (report.reportType === "manual") {
    return "manual";
  }

  return "auto";
}

function reportTypeBadgeClassName(reportType?: string) {
  if (reportType === "telegram" || reportType === "meeting") {
    return "report-type-badge manual";
  }

  if (reportType === "manual") {
    return "report-type-badge manual";
  }

  return "report-type-badge auto";
}

function formatTelegramEvent(eventType?: string, status?: string) {
  if (status === "break_closed") {
    return "break off";
  }

  const labels: Record<string, string> = {
    online: "online",
    afk: "afk",
    offline: "offline",
    telegram_online: "online",
    telegram_afk: "afk",
    telegram_offline: "offline"
  };

  return labels[eventType ?? ""] ?? "telegram";
}

function formatDiscordEvent(eventType?: string) {
  const labels: Record<string, string> = {
    join: "meeting join",
    leave: "meeting leave",
    reconcile: "meeting live",
    meeting_join: "meeting join",
    meeting_leave: "meeting leave",
    meeting_reconcile: "meeting live"
  };

  return labels[eventType ?? ""] ?? "meeting";
}

function formatActivityType(type: string) {
  const labels: Record<string, string> = {
    external: "External",
    play_mode: "Play Mode",
    prefab_saved: "Prefab Save",
    file_loaded: "File Load",
    file_saved: "File Save",
    scene_changed: "Scene Change",
    scene_saved: "Scene Save",
    select: "Select",
    undo_redo: "Undo/Redo"
  };

  return labels[type] ?? type;
}

function formatAuthorTime(report: Report) {
  if (!report.recordedAt) {
    return "-";
  }

  const recordedAt = new Date(report.recordedAt);

  try {
    return new Intl.DateTimeFormat(undefined, {
      timeStyle: "short",
      timeZone: report.timeZoneId
    }).format(recordedAt);
  } catch {
    return formatOffsetTimestampTime(report.recordedAt);
  }
}

function formatOffsetTimestampTime(value: string) {
  const match = value.match(/T(\d{2}):(\d{2})/);

  if (match) {
    return `${match[1]}:${match[2]}`;
  }

  return new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date(value));
}

function formatTimeZoneLabel(report: Report) {
  if (report.timeZoneId) {
    const city = report.timeZoneId.split("/").pop()?.replace(/_/g, " ");

    if (city) {
      return city;
    }
  }

  return report.timeZoneDisplayName;
}

function formatProfileTimeZoneLabel(profile: AuthorProfile) {
  if (profile.timeZoneId) {
    const city = profile.timeZoneId.split("/").pop()?.replace(/_/g, " ");

    if (city) {
      return city;
    }
  }

  return profile.timeZoneDisplayName || "-";
}

function formatProfileTimeZoneTitle(profile: AuthorProfile) {
  if (profile.timeZoneId && profile.timeZoneDisplayName && profile.timeZoneDisplayName !== profile.timeZoneId) {
    return `${profile.timeZoneDisplayName} (${profile.timeZoneId})`;
  }

  return profile.timeZoneId || profile.timeZoneDisplayName || "Timezone will be detected from plugin reports";
}

function formatTimestamp(value?: string) {
  if (!value) {
    return "-";
  }

  return new Date(value).toLocaleString();
}

function matchesAuthorSearch(author: AuthorRow, search: string) {
  const query = search.trim().toLowerCase();

  if (!query) {
    return true;
  }

  return [author.displayName, author.authorEmail, author.rawAuthor, author.team, author.telegramUsername].some((value) =>
    value?.toLowerCase().includes(query)
  );
}

function formatAuthorStatus(author: AuthorRow) {
  if (author.status === "stale") {
    if (isTelegramSignedOff(author.stalePresence)) {
      return "Offline";
    }

    return author.lastReceivedAt ? "Offline" : "No reports";
  }

  return "Online";
}

function authorStatusBadgeClassName(status?: "online" | "stale", stalePresence?: AuthorRow["stalePresence"]) {
  if (status === "stale") {
    if (isTelegramSignedOff(stalePresence)) {
      return "status-badge telegram-signed-off";
    }

    return "status-badge stale";
  }

  return "status-badge online";
}

function authorCardClassName(author: AuthorRow, active: boolean) {
  let presenceClass = "is-online";

  if (author.status === "stale") {
    presenceClass = isTelegramSignedOff(author.stalePresence) ? "is-telegram-offline" : "is-offline";
  }

  return `author-card ${active ? "active " : ""}${presenceClass} ${productivityTone(author.productivity)}`.trim();
}

function isTelegramSignedOff(stalePresence?: AuthorRow["stalePresence"]) {
  return stalePresence === "telegram" || stalePresence === "both";
}

function compareAuthorCardStatus(left: AuthorRow, right: AuthorRow, dateRange: DateRange) {
  const leftSignedOff = isTelegramSignedOff(left.stalePresence);
  const rightSignedOff = isTelegramSignedOff(right.stalePresence);

  if (leftSignedOff !== rightSignedOff) {
    return leftSignedOff ? 1 : -1;
  }

  if (left.status !== right.status) {
    return left.status === "stale" ? 1 : -1;
  }

  if (left.status === "stale") {
    const leftLiveDate = isSelectedDateAuthorLocalToday(left, dateRange) && hasAuthorActivity(left);
    const rightLiveDate = isSelectedDateAuthorLocalToday(right, dateRange) && hasAuthorActivity(right);

    if (leftLiveDate !== rightLiveDate) {
      return leftLiveDate ? -1 : 1;
    }
  }

  return compareAuthorsByStatusAndProductivity(left, right);
}

function compareAuthorsByStatusAndProductivity(left: AuthorRow, right: AuthorRow) {
  if (left.status !== right.status) {
    return left.status === "stale" ? 1 : -1;
  }

  const leftProd = Number.isFinite(left.productivity) ? left.productivity : 0;
  const rightProd = Number.isFinite(right.productivity) ? right.productivity : 0;
  const byProductivity = rightProd - leftProd;

  if (byProductivity !== 0) {
    return byProductivity;
  }

  return left.displayName.localeCompare(right.displayName);
}

function isSelectedDateAuthorLocalToday(author: AuthorRow, dateRange: DateRange) {
  if (dateRange.startDate !== dateRange.endDate || dateRange.preset === "live") {
    return false;
  }

  return dateRange.startDate === authorLocalDate(author);
}

function authorLocalDate(author: AuthorRow) {
  const now = new Date();
  const timeZone = author.timeZoneId;

  if (!timeZone) {
    return toDateInputValue(now);
  }

  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(now);
    const year = parts.find((part) => part.type === "year")?.value;
    const month = parts.find((part) => part.type === "month")?.value;
    const day = parts.find((part) => part.type === "day")?.value;

    if (year && month && day) {
      return `${year}-${month}-${day}`;
    }
  } catch {
    return toDateInputValue(now);
  }

  return toDateInputValue(now);
}

function alertCardClassName(severity: AuthorAlert["severity"]) {
  return `alert-card ${severity}`;
}

function alertAuthorCardClassName(stats: AlertStats) {
  if (stats.critical) {
    return "alert-author-card critical";
  }

  if (stats.warning) {
    return "alert-author-card warning";
  }

  return "alert-author-card healthy";
}

function alertCountBadgeClassName(tone: "total" | "critical" | "warning" | "healthy" | "muted") {
  return `alert-count-badge ${tone}`;
}

function alertSeverityBadgeClassName(severity: AuthorAlert["severity"]) {
  return `alert-severity-badge ${severity}`;
}

function alertKey(alert: AuthorAlert, rawAuthor: string, index: number) {
  return alert.id ?? `${rawAuthor}:${alert.type}:${alert.createdAt ?? ""}:${alert.source ?? ""}:${alert.deviceId ?? ""}:${index}`;
}

function compareAlertAuthors(left: AuthorRow, right: AuthorRow) {
  const leftStats = left.alertStats ?? { total: 0, critical: 0, warning: 0 };
  const rightStats = right.alertStats ?? { total: 0, critical: 0, warning: 0 };

  return (
    rightStats.critical - leftStats.critical
    || rightStats.warning - leftStats.warning
    || rightStats.total - leftStats.total
    || left.displayName.localeCompare(right.displayName)
  );
}

function formatAlertValue(alert: AuthorAlert) {
  if (alert.type === "report_forgery_attempt") {
    const parts = [alert.source, alert.pluginVersion, alert.deviceId ? `device ${alert.deviceId.slice(0, 8)}` : "", alert.createdAt ? formatTimestamp(alert.createdAt) : ""].filter(Boolean);
    return parts.length ? parts.join(" · ") : "Suspicious report was rejected.";
  }

  if (alert.value === null || alert.value === undefined) {
    return `Threshold: ${formatAlertThreshold(alert)}`;
  }

  if (alert.type === "reports_stopped") {
    return `No reports for ${formatDuration(alert.value)}. Threshold: ${formatAlertThreshold(alert)}`;
  }

  if (alert.type === "long_break") {
    return `Break: ${formatDuration(alert.value)}. Threshold: ${formatAlertThreshold(alert)}`;
  }

  if (alert.type === "telegram_day_open") {
    return `Open for ${formatDuration(alert.value)}. Capped at ${formatAlertThreshold(alert)}`;
  }

  if (alert.type === "low_productivity" || alert.type === "select_heavy_activity") {
    return `Value: ${alert.value}%. Threshold: ${formatAlertThreshold(alert)}`;
  }

  return `Value: ${alert.value}. Threshold: ${formatAlertThreshold(alert)}`;
}

function formatAlertThreshold(alert: AuthorAlert) {
  if (alert.threshold === null || alert.threshold === undefined) {
    return "-";
  }

  if (alert.type === "reports_stopped" || alert.type === "long_break" || alert.type === "telegram_day_open") {
    return formatDuration(alert.threshold);
  }

  if (alert.type === "low_productivity" || alert.type === "select_heavy_activity") {
    return `${alert.threshold}%`;
  }

  return String(alert.threshold);
}

function initials(value: string) {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function avatarStyle(authorColor?: string) {
  return authorColor ? { backgroundColor: authorColor } : undefined;
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

function loadSavedActivityAuthor() {
  const savedAuthor = localStorage.getItem(ACTIVITY_AUTHOR_STORAGE_KEY);

  if (savedAuthor && savedAuthor.trim()) {
    return savedAuthor;
  }

  return null;
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

createRoot(document.getElementById("root")!).render(<App />);
