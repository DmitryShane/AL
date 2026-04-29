import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, BarChart3, Bell, Box, Boxes, CalendarDays, LogOut, RefreshCw, Search, Settings, ShieldCheck, UsersRound } from "lucide-react";
import { AuthorsTable } from "./components/AuthorsTable";
import { HourlyActivityChart } from "./components/HourlyActivityChart";
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
  pluginVersion?: string;
};

type AuthorRow = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  telegramUsername?: string;
  authorColor?: string;
  source?: string;
  pluginVersion?: string;
  lastRecordedAt?: string;
  lastReceivedAt?: string;
  daySeconds: number;
  telegramDaySeconds: number;
  pluginDaySeconds: number;
  activeSeconds: number;
  idleSeconds: number;
  breakSeconds: number;
  overtimeActiveSeconds: number;
  productivity: number;
  activityMix?: ActivityCount[];
  savedPrefabs?: SavedPrefab[];
  status?: "online" | "stale";
  alerts?: AuthorAlert[];
  alertStats?: AlertStats;
};

type AuthorAlert = {
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
    activeSeconds: number;
    idleSeconds: number;
    breakSeconds: number;
    overtimeActiveSeconds: number;
  };
  authors: AuthorRow[];
  profiles: AuthorProfile[];
  activityMix: ActivityCount[];
  savedPrefabs: SavedPrefab[];
  hourlyActivityByAuthor: AuthorHourlyActivity[];
};

type AuthorProfile = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  telegramUsername?: string;
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
  saveCount: number;
};

type HourlyActivity = {
  hour: number;
  activeSeconds: number;
  idleSeconds: number;
  breakSeconds?: number;
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
};

type AnalyticsDelta = {
  activeSeconds: number;
  idleSeconds: number;
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
};

const emptyActivitySummary: ActivitySummary = {
  totals: {
    daySeconds: 0,
    telegramDaySeconds: 0,
    pluginDaySeconds: 0,
    activeSeconds: 0,
    idleSeconds: 0,
    breakSeconds: 0,
    overtimeActiveSeconds: 0
  },
  authors: [],
  profiles: [],
  activityMix: [],
  savedPrefabs: [],
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

    try {
      const params = new URLSearchParams({
        startDate: dateRange.startDate,
        endDate: dateRange.endDate
      });

      if (dateRangePreset(dateRange) === "today") {
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
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [authUser?.email, dateRange.startDate, dateRange.endDate]);

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
  }, [authUser?.email, dateRange.startDate, dateRange.endDate, dashboardRefreshMs]);

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
          <div>
            <h1>{pageTitle(page)}</h1>
            <p>{pageSubtitle(page)}</p>
          </div>
          {page === "authors" || page === "activity" || page === "alerts" ? (
            <div className="topbar-actions">
              <DateRangePicker value={dateRange} onChange={setDateRange} />
            </div>
          ) : null}
        </header>

        {authLoading ? <p className="notice">Restoring dashboard session...</p> : null}
        {!authLoading && loading ? <p className="notice">Loading dashboard data...</p> : null}
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
            reports={summary?.reports ?? []}
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
        throw new Error("Invalid email or password");
      }

      const payload = await response.json();
      onLogin(payload.user);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Login failed");
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

      <AuthorsTable authors={authors} emptyMessage="No authors match this search." />
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

          {selected ? <AnalyticsHierarchy author={selected} year={analytics.year} /> : <p className="empty">No analytics authors yet.</p>}
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

function AnalyticsHierarchy({ author, year }: { author: AnalyticsAuthorSummary; year: number }) {
  const [expandedMonths, setExpandedMonths] = useState<Record<number, boolean>>({});
  const [expandedWeeks, setExpandedWeeks] = useState<Record<string, boolean>>({});

  function toggleMonth(month: number) {
    setExpandedMonths((items) => ({ ...items, [month]: !items[month] }));
  }

  function toggleWeek(month: number, week: number) {
    const key = `${month}-${week}`;
    setExpandedWeeks((items) => ({ ...items, [key]: !items[key] }));
  }

  return (
    <section className="analytics-hierarchy">
      <div className="analytics-selected-author">
        <span className="avatar" style={avatarStyle(author.authorColor)}>{initials(author.displayName)}</span>
        <div>
          <strong>{author.displayName}</strong>
          <small title={author.authorEmail || author.rawAuthor}>{author.authorEmail || author.rawAuthor}</small>
          <small>{author.team || "No team"} · {year}</small>
        </div>
      </div>

      <div className="analytics-tree">
        {author.months.map((month) => {
          const monthOpen = expandedMonths[month.month] ?? month.month === new Date().getMonth() + 1;

          return (
            <section className="analytics-tree-card" key={month.month}>
              <button className="analytics-tree-toggle month" onClick={() => toggleMonth(month.month)}>
                <span>{monthOpen ? "−" : "+"}</span>
                <div className="analytics-month-title">
                  <strong>{month.label}</strong>
                  <small>{year}</small>
                </div>
                <AnalyticsPeriodSummary totals={month.totals} />
              </button>
              <AnalyticsMetricsTable totals={month.totals} delta={month.previousMonthDeltas} deltaLabel="vs previous month" />

              {monthOpen ? (
                <div className="analytics-week-list">
                  {month.weeks.map((week) => {
                    const weekKey = `${month.month}-${week.week}`;
                    const weekOpen = expandedWeeks[weekKey] ?? false;

                    return (
                      <section className="analytics-week-card" key={weekKey}>
                        <button className="analytics-tree-toggle week" onClick={() => toggleWeek(month.month, week.week)}>
                          <span>{weekOpen ? "−" : "+"}</span>
                          <div>
                            <strong>Week {week.week}</strong>
                            <small>{week.label}</small>
                          </div>
                          <AnalyticsPeriodSummary totals={week.totals} compact />
                        </button>
                        <AnalyticsMetricsTable totals={week.totals} delta={week.previousWeekDeltas} deltaLabel="vs previous week" />

                        {weekOpen ? (
                          <div className="analytics-day-table">
                            <div className="analytics-table-head">
                              <span>Day</span>
                              <span>Plugin Day</span>
                              <span>Active</span>
                              <span>Idle</span>
                              <span>Overtime</span>
                              <span>Break</span>
                              <span>Productivity</span>
                            </div>
                            {week.days.map((day) => (
                              <div className={day.inMonth ? "analytics-day-row" : "analytics-day-row muted"} key={day.date}>
                                <span>{day.label}</span>
                                <strong>{formatDuration(day.totals.pluginDaySeconds)}</strong>
                                <strong>{formatDuration(day.totals.activeSeconds)}</strong>
                                <span>{formatDuration(day.totals.idleSeconds)}</span>
                                <strong>{formatDuration(day.totals.overtimeActiveSeconds)}</strong>
                                <span className={breakClassName(day.totals.breakSeconds)}>{formatMinutes(day.totals.breakSeconds)}</span>
                                <strong className={productivityClassName(day.totals.productivity)}>{day.totals.productivity.toFixed(2)}%</strong>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </section>
                    );
                  })}
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
    </section>
  );
}

function AnalyticsMetricsTable({ totals, delta, deltaLabel }: { totals: AnalyticsTotals; delta: AnalyticsDelta; deltaLabel: string }) {
  return (
    <div className="analytics-metrics-table">
      <div className="analytics-table-head">
        <span>Period</span>
        <span>Plugin Day</span>
        <span>Active</span>
        <span>Idle</span>
        <span>Overtime</span>
        <span>Break</span>
        <span>Productivity</span>
      </div>
      <div className="analytics-day-row">
        <span>Total</span>
        <strong>{formatDuration(totals.pluginDaySeconds)}</strong>
        <strong>{formatDuration(totals.activeSeconds)}</strong>
        <span>{formatDuration(totals.idleSeconds)}</span>
        <strong>{formatDuration(totals.overtimeActiveSeconds)}</strong>
        <span className={breakClassName(totals.breakSeconds)}>{formatMinutes(totals.breakSeconds)}</span>
        <strong className={productivityClassName(totals.productivity)}>{totals.productivity.toFixed(2)}%</strong>
      </div>
      <div className="analytics-delta-row">
        <span>{deltaLabel}</span>
        <AnalyticsDeltaValue value={delta.pluginDaySeconds} />
        <AnalyticsDeltaValue value={delta.activeSeconds} />
        <AnalyticsDeltaValue value={delta.idleSeconds} inverse />
        <AnalyticsDeltaValue value={delta.overtimeActiveSeconds} />
        <AnalyticsDeltaValue value={delta.breakSeconds} inverse />
        <AnalyticsDeltaValue value={delta.productivity} percent />
      </div>
    </div>
  );
}

function AnalyticsPeriodSummary({ totals, compact = false }: { totals: AnalyticsTotals; compact?: boolean }) {
  return (
    <div className={compact ? "analytics-period-summary compact" : "analytics-period-summary"}>
      <span>{formatDuration(totals.pluginDaySeconds)} day</span>
      <span>{formatDuration(totals.overtimeActiveSeconds)} overtime</span>
      <span className={productivityClassName(totals.productivity)}>{totals.productivity.toFixed(1)}%</span>
    </div>
  );
}

function AnalyticsDeltaValue({ value, percent = false, inverse = false }: { value: number; percent?: boolean; inverse?: boolean }) {
  const isPositive = inverse ? value <= 0 : value >= 0;
  return <span className={isPositive ? "positive" : "negative"}>{percent ? formatDelta(value) : formatDurationDelta(value)}</span>;
}

function AlertsPage({ authors }: { authors: AuthorRow[] }) {
  return (
    <section className="page-section">
      <div className="alerts-grid">
        {authors.map((author) => {
          const alerts = author.alerts ?? [];
          const stats = author.alertStats ?? { total: 0, critical: 0, warning: 0 };

          return (
            <article className="alert-author-card" key={author.rawAuthor}>
              <div className="alert-author-header">
                <span className="avatar" style={avatarStyle(author.authorColor)}>{initials(author.displayName)}</span>
                <div>
                  <strong>{author.displayName}</strong>
                  <small>{author.authorEmail || author.rawAuthor}</small>
                  <small>{author.team || "No team"}</small>
                </div>
                <span className={authorStatusBadgeClassName(author.status)}>{formatAuthorStatus(author)}</span>
              </div>

              <div className="alert-stat-row">
                <div>
                  <span>Total</span>
                  <strong>{stats.total}</strong>
                </div>
                <div>
                  <span>Critical</span>
                  <strong>{stats.critical}</strong>
                </div>
                <div>
                  <span>Warning</span>
                  <strong>{stats.warning}</strong>
                </div>
              </div>

              <div className="alert-stack">
                {alerts.length ? (
                  alerts.map((alert) => (
                    <div className={alertCardClassName(alert.severity)} key={alert.type}>
                      <div>
                        <strong>{alert.title}</strong>
                        <span>{alert.severity}</span>
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
      {!authors.length ? <p className="empty">No authors for the selected period.</p> : null}
    </section>
  );
}

function ActivityPage({
  summary,
  reports,
  selectedAuthor,
  setSelectedAuthor,
  refreshing,
  onRefreshAuthor
}: {
  summary: ActivitySummary;
  reports: Report[];
  selectedAuthor: string | null;
  setSelectedAuthor: (value: string) => void;
  refreshing: boolean;
  onRefreshAuthor: (author: string) => void;
}) {
  const author = summary.authors.find((item) => item.rawAuthor === selectedAuthor) ?? summary.authors[0];
  const hourly = summary.hourlyActivityByAuthor.filter((item) => item.rawAuthor === author?.rawAuthor);
  const authorHourly = hourly.length || !author
    ? hourly
    : [{ author: author.displayName, rawAuthor: author.rawAuthor, hourlyActivity: [] }];
  const authorReports = reports.filter((report) => report.author === author?.rawAuthor);
  const activityMix = author?.activityMix ?? [];
  const savedPrefabs = author?.savedPrefabs ?? [];

  return (
    <section className="page-section">
      <div className="author-card-strip">
        {summary.authors.map((item) => (
          <button
            className={item.rawAuthor === author?.rawAuthor ? "author-card active" : "author-card"}
            key={item.rawAuthor}
            onClick={() => setSelectedAuthor(item.rawAuthor)}
          >
            <span className="avatar" style={avatarStyle(item.authorColor)}>{initials(item.displayName)}</span>
            <strong>{item.displayName}</strong>
            <small>{item.team || "No team"}</small>
            <div className="author-card-footer">
              <div className="mini-metrics">
                <span>{formatDuration(item.activeSeconds)} active</span>
                <span>{formatDuration(item.idleSeconds)} idle</span>
                <span>{formatDuration(item.breakSeconds)} break</span>
              </div>
              <div className={`productivity-badge ${productivityTone(item.productivity)}`}>
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
            <Duration label="Day Time (Plugin)" seconds={author.pluginDaySeconds ?? author.activeSeconds + author.idleSeconds} />
            <Duration label="Active" seconds={author.activeSeconds} />
            <Duration label="Idle" seconds={author.idleSeconds} />
            <Duration label="Overtime" seconds={author.overtimeActiveSeconds} />
            <Duration label="Break" seconds={author.breakSeconds} valueClassName={breakClassName(author.breakSeconds)} />
            <div className="duration">
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
                label: prefab.name || prefab.path,
                value: prefab.saveCount,
                displayValue: String(prefab.saveCount),
                color: paletteColor(index)
              }))}
            />
          </div>

          <ReportsTable reports={authorReports} />
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
  const [settingsTab, setSettingsTab] = useState<"authors" | "users">("authors");
  const [drafts, setDrafts] = useState<Record<string, AuthorProfile>>({});
  const [globalInterval, setGlobalInterval] = useState(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
  const [saving, setSaving] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, "saved" | "error" | undefined>>({});
  const [deleteTarget, setDeleteTarget] = useState<AuthorProfile | null>(null);
  const [deleteProfileTarget, setDeleteProfileTarget] = useState<AuthorProfile | null>(null);
  const [newProfile, setNewProfile] = useState<AuthorProfile>(() => emptyAuthorProfile());

  useEffect(() => {
    const nextDrafts: Record<string, AuthorProfile> = {};

    for (const profile of profiles) {
      nextDrafts[profile.rawAuthor] = { ...profile };
    }

    setDrafts(nextDrafts);
    setGlobalInterval(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
  }, [summary]);

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

  function isProfileDirty(profile: AuthorProfile) {
    const draft = drafts[profile.rawAuthor] ?? profile;

    return (
      (draft.displayName ?? "") !== (profile.displayName ?? "") ||
      (draft.team ?? "") !== (profile.team ?? "") ||
      (draft.telegramUsername ?? "") !== (profile.telegramUsername ?? "") ||
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
        <h2>Author Profiles</h2>
        <p className="settings-caption">
          Telegram username links chat messages to the author. Use the same username from the work chat, with or without @.
          Raw Author must exactly match the value that Unity or Blender will send from git config user.name.
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

function ReportsTable({ reports }: { reports: Report[] }) {
  const pageSizeOptions = [10, 25, 50];
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(reports.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * pageSize;
  const pageReports = reports.slice(pageStart, pageStart + pageSize);

  useEffect(() => {
    setPage(1);
  }, [pageSize, reports.length]);

  return (
    <section className="panel table-panel">
      <h2>Plugin Reports</h2>
      <div className="table">
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
        {pageReports.map((report, index) => (
          <div className="table-row" key={`${report.recordedAt ?? "report"}-${index}`}>
            <span className="source-cell">{sourceIcon(report.source)}{formatSource(report.source)}</span>
            <span>{report.displayName ?? report.author ?? "Unknown User"}</span>
            <span>{report.date ?? "-"}</span>
            <span>{formatReportMinutes(report.activeDeltaSeconds ?? 0)}</span>
            <span>{formatReportMinutes(report.idleDeltaSeconds ?? 0)}</span>
            <span>{formatReportOvertime(report.overtimeActiveDeltaSeconds ?? 0)}</span>
            <span>{formatAuthorTime(report)}</span>
            <span className={reportTypeBadgeClassName(report.reportType)}>{formatReportType(report)}</span>
            <span>{formatTimeZoneLabel(report) ?? "-"}</span>
          </div>
        ))}
      </div>
      <div className="table-pagination">
        <span>
          Rows {reports.length ? pageStart + 1 : 0}-{Math.min(pageStart + pageSize, reports.length)} of {reports.length}
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
          <button className="primary-outline-button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={currentPage === 1}>
            Prev
          </button>
          <span>{currentPage} / {totalPages}</span>
          <button className="primary-outline-button" onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={currentPage === totalPages}>
            Next
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
        <div className="list">
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

function DateRangePicker({ value, onChange }: { value: DateRange; onChange: (range: DateRange) => void }) {
  const activePreset = dateRangePreset(value);

  return (
    <div className="date-range-group">
      <div className="date-presets" aria-label="Date presets">
        <button className={activePreset === "today" ? "active" : undefined} onClick={() => onChange(todayRange())}>Today</button>
        <button className={activePreset === "week" ? "active" : undefined} onClick={() => onChange(currentWeekRange())}>Week</button>
        <button className={activePreset === "month" ? "active" : undefined} onClick={() => onChange(currentMonthRange())}>Month</button>
      </div>
      <div className="date-range-control">
        <CalendarDays size={18} />
        <input type="date" value={value.startDate} onChange={(event) => onChange({ ...value, startDate: event.target.value })} />
        <span>to</span>
        <input type="date" value={value.endDate} onChange={(event) => onChange({ ...value, endDate: event.target.value })} />
      </div>
    </div>
  );
}

function dateRangePreset(value: DateRange) {
  if (sameDateRange(value, todayRange())) {
    return "today";
  }

  if (sameDateRange(value, currentWeekRange())) {
    return "week";
  }

  if (sameDateRange(value, currentMonthRange())) {
    return "month";
  }

  return "custom";
}

function sameDateRange(left: DateRange, right: DateRange) {
  return left.startDate === right.startDate && left.endDate === right.endDate;
}

function Duration({ label, seconds, valueClassName }: { label: string; seconds: number; valueClassName?: string }) {
  return (
    <div className="duration">
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
  if (productivity > 80) {
    return "metric-value good";
  }

  if (productivity >= 50) {
    return "metric-value warning";
  }

  return "metric-value bad";
}

function productivityTone(productivity: number) {
  if (productivity > 80) {
    return "good";
  }

  if (productivity >= 50) {
    return "warning";
  }

  return "bad";
}

function breakClassName(seconds: number) {
  return seconds > 3600 ? "metric-value bad" : "metric-value";
}

function formatSource(source?: string) {
  if (source === "ual") {
    return "Unity";
  }

  if (source === "bal") {
    return "Blender";
  }

  if (source === "telegram") {
    return "Telegram";
  }

  return source ?? "-";
}

function sourceIcon(source?: string) {
  if (source === "ual") {
    return <Box size={16} />;
  }

  if (source === "bal") {
    return <Boxes size={16} />;
  }

  return <Activity size={16} />;
}

function formatReportType(report: Report) {
  if (report.reportType === "telegram") {
    return formatTelegramEvent(report.telegramEventType ?? report.activityType);
  }

  if (report.reportType === "manual") {
    return "manual";
  }

  return "auto";
}

function reportTypeBadgeClassName(reportType?: string) {
  if (reportType === "telegram") {
    return "report-type-badge manual";
  }

  if (reportType === "manual") {
    return "report-type-badge manual";
  }

  return "report-type-badge auto";
}

function formatTelegramEvent(eventType?: string) {
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
    return author.lastReceivedAt ? "Offline" : "No reports";
  }

  return "Online";
}

function authorStatusBadgeClassName(status?: "online" | "stale") {
  return status === "stale" ? "status-badge stale" : "status-badge online";
}

function alertCardClassName(severity: AuthorAlert["severity"]) {
  return `alert-card ${severity}`;
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

  if (alert.type === "low_productivity" || alert.type === "select_heavy_activity") {
    return `Value: ${alert.value}%. Threshold: ${formatAlertThreshold(alert)}`;
  }

  return `Value: ${alert.value}. Threshold: ${formatAlertThreshold(alert)}`;
}

function formatAlertThreshold(alert: AuthorAlert) {
  if (alert.threshold === null || alert.threshold === undefined) {
    return "-";
  }

  if (alert.type === "reports_stopped" || alert.type === "long_break") {
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
  return { startDate: today, endDate: today };
}

function currentWeekRange(): DateRange {
  const end = new Date();
  const start = new Date(end);
  start.setDate(end.getDate() - 7);
  return { startDate: toDateInputValue(start), endDate: toDateInputValue(end) };
}

function currentMonthRange(): DateRange {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), 1);
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  return { startDate: toDateInputValue(start), endDate: toDateInputValue(end) };
}

function toDateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

createRoot(document.getElementById("root")!).render(<App />);
