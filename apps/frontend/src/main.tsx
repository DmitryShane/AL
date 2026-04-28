import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, CalendarDays, RefreshCw, Search, Settings, UsersRound } from "lucide-react";
import { HourlyActivityChart } from "./components/HourlyActivityChart";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";
const REFRESH_INTERVAL_MS = 10000;

type Page = "authors" | "activity" | "settings";

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
  pluginVersion?: string;
};

type AuthorRow = {
  rawAuthor: string;
  displayName: string;
  team?: string;
  telegramUsername?: string;
  source?: string;
  pluginVersion?: string;
  lastRecordedAt?: string;
  daySeconds: number;
  activeSeconds: number;
  idleSeconds: number;
  breakSeconds: number;
  overtimeActiveSeconds: number;
  productivity: number;
};

type ActivitySummary = {
  totals: {
    daySeconds: number;
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
  displayName: string;
  team?: string;
  telegramUsername?: string;
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

type DateRange = {
  startDate: string;
  endDate: string;
};

const emptyActivitySummary: ActivitySummary = {
  totals: {
    daySeconds: 0,
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
  const [page, setPage] = useState<Page>("authors");
  const [health, setHealth] = useState<Health | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [dateRange, setDateRange] = useState<DateRange>(() => todayRange());
  const [search, setSearch] = useState("");
  const [selectedAuthor, setSelectedAuthor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load(showLoading = true) {
    if (showLoading) {
      setLoading(true);
    }

    setError(null);

    try {
      const params = new URLSearchParams({
        startDate: dateRange.startDate,
        endDate: dateRange.endDate
      });
      const [healthResponse, summaryResponse] = await Promise.all([
        fetch(`${API_URL}/api/v1/health`),
        fetch(`${API_URL}/api/v1/reports/summary?${params.toString()}`)
      ]);

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
  }, [dateRange.startDate, dateRange.endDate]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      void load(false);
    }, REFRESH_INTERVAL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [dateRange.startDate, dateRange.endDate]);

  const activitySummary = summary?.activitySummary ?? emptyActivitySummary;
  const authors = useMemo(() => activitySummary.authors.filter((author) => matchesAuthorSearch(author, search)), [activitySummary, search]);
  const activeAuthor = selectedAuthor ?? authors[0]?.rawAuthor ?? activitySummary.authors[0]?.rawAuthor ?? null;

  useEffect(() => {
    if (!activeAuthor && activitySummary.authors.length) {
      setSelectedAuthor(activitySummary.authors[0].rawAuthor);
    }
  }, [activeAuthor, activitySummary.authors]);

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <div className="brand-mark">
          <span>AL</span>
          <strong>Activity Logger</strong>
        </div>
        <nav className="side-nav">
          <NavButton icon={<UsersRound size={20} />} label="Authors" active={page === "authors"} onClick={() => setPage("authors")} />
          <NavButton icon={<Activity size={20} />} label="Activity" active={page === "activity"} onClick={() => setPage("activity")} />
          <NavButton icon={<Settings size={20} />} label="Settings" active={page === "settings"} onClick={() => setPage("settings")} />
        </nav>
      </aside>

      <main className="workspace">
        <header className="workspace-topbar">
          <div>
            <h1>{pageTitle(page)}</h1>
            <p>{pageSubtitle(page)}</p>
          </div>
          <div className="topbar-actions">
            <DateRangePicker value={dateRange} onChange={setDateRange} />
          </div>
        </header>

        {loading ? <p className="notice">Loading dashboard data...</p> : null}
        {error ? <p className="notice error">{error}</p> : null}

        {page === "authors" ? (
          <AuthorsPage authors={authors} search={search} setSearch={setSearch} onRefresh={() => void load()} />
        ) : null}
        {page === "activity" ? (
          <ActivityPage
            summary={activitySummary}
            reports={summary?.reports ?? []}
            selectedAuthor={activeAuthor}
            setSelectedAuthor={setSelectedAuthor}
          />
        ) : null}
        {page === "settings" ? <SettingsPage summary={summary} health={health} onSaved={() => void load(false)} /> : null}
      </main>
    </div>
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
  onRefresh
}: {
  authors: AuthorRow[];
  search: string;
  setSearch: (value: string) => void;
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
        <button className="primary-outline-button" onClick={onRefresh}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </div>

      <div className="authors-table">
        <div className="authors-table-head">
          <span>Author</span>
          <span>Team</span>
          <span>Day Time</span>
          <span>Active Time</span>
          <span>Idle Time</span>
          <span>Break Time</span>
          <span>Productivity</span>
          <span>Plugin</span>
          <span>Version</span>
          <span>Last Report</span>
        </div>
        {authors.map((author) => (
          <div className="authors-row" key={author.rawAuthor}>
            <div className="author-cell" title={author.rawAuthor}>
              <span className="avatar">{initials(author.displayName)}</span>
              <div>
                <strong>{author.displayName}</strong>
                <small>{author.rawAuthor}</small>
              </div>
            </div>
            <span>{author.team || "-"}</span>
            <strong>{formatDuration(author.daySeconds)}</strong>
            <strong>{formatDuration(author.activeSeconds)}</strong>
            <span>{formatDuration(author.idleSeconds)}</span>
            <span className={breakClassName(author.breakSeconds)}>{formatMinutes(author.breakSeconds)}</span>
            <strong className={productivityClassName(author.productivity)}>{author.productivity.toFixed(2)}%</strong>
            <span>{formatSource(author.source)}</span>
            <span>{author.pluginVersion ?? "-"}</span>
            <span>{formatTimestamp(author.lastRecordedAt)}</span>
          </div>
        ))}
        {!authors.length ? <p className="empty-table">No authors match this search.</p> : null}
      </div>
    </section>
  );
}

function ActivityPage({
  summary,
  reports,
  selectedAuthor,
  setSelectedAuthor
}: {
  summary: ActivitySummary;
  reports: Report[];
  selectedAuthor: string | null;
  setSelectedAuthor: (value: string) => void;
}) {
  const author = summary.authors.find((item) => item.rawAuthor === selectedAuthor) ?? summary.authors[0];
  const hourly = summary.hourlyActivityByAuthor.filter((item) => item.rawAuthor === author?.rawAuthor);
  const authorReports = reports.filter((report) => report.author === author?.rawAuthor);

  return (
    <section className="page-section">
      <div className="author-card-strip">
        {summary.authors.map((item) => (
          <button
            className={item.rawAuthor === author?.rawAuthor ? "author-card active" : "author-card"}
            key={item.rawAuthor}
            onClick={() => setSelectedAuthor(item.rawAuthor)}
          >
            <span className="avatar">{initials(item.displayName)}</span>
            <strong>{item.displayName}</strong>
            <small>{item.team || "No team"}</small>
            <div className="mini-metrics">
              <span>{formatDuration(item.activeSeconds)} active</span>
              <span>{formatDuration(item.idleSeconds)} idle</span>
              <span>{formatDuration(item.breakSeconds)} break</span>
            </div>
          </button>
        ))}
      </div>

      {author ? (
        <>
          <div className="activity-grid">
            <Duration label="Day Time" seconds={author.daySeconds} />
            <Duration label="Active" seconds={author.activeSeconds} />
            <Duration label="Idle" seconds={author.idleSeconds} />
            <Duration label="Break" seconds={author.breakSeconds} />
            <div className="duration">
              <span>Productivity</span>
              <strong>{author.productivity.toFixed(2)}%</strong>
            </div>
          </div>

          <HourlyActivityChart authors={hourly} />

          <div className="content-grid compact">
            <PanelList title="Activity Mix" items={summary.activityMix.map((item) => [formatActivityType(item.type), `${item.percent}%`])} />
            <PanelList title="Saved Prefabs" items={summary.savedPrefabs.map((prefab) => [prefab.name || prefab.path, String(prefab.saveCount)])} />
          </div>

          <ReportsTable reports={authorReports} />
        </>
      ) : (
        <p className="empty">No author activity for this period.</p>
      )}
    </section>
  );
}

function SettingsPage({ summary, health, onSaved }: { summary: Summary | null; health: Health | null; onSaved: () => void }) {
  const profiles = summary?.activitySummary.profiles ?? [];
  const [drafts, setDrafts] = useState<Record<string, AuthorProfile>>({});
  const [globalInterval, setGlobalInterval] = useState(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
  const [saving, setSaving] = useState<string | null>(null);

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
    await fetch(`${API_URL}/api/v1/authors/profile`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile)
    });
    setSaving(null);
    onSaved();
  }

  async function saveInterval() {
    setSaving("interval");
    await fetch(`${API_URL}/api/v1/settings/intervals`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ defaultSendIntervalSeconds: Number(globalInterval) })
    });
    setSaving(null);
    onSaved();
  }

  return (
    <section className="page-section settings-layout">
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
          <button className="primary-button" onClick={() => void saveInterval()} disabled={saving === "interval"}>
            Save
          </button>
        </div>
      </div>

      <div className="panel">
        <h2>Author Profiles</h2>
        <div className="profile-table">
          <div className="profile-table-head">
            <span>Raw Author</span>
            <span>Display Name</span>
            <span>Team</span>
            <span>Telegram</span>
            <span />
          </div>
          {profiles.map((profile) => {
            const draft = drafts[profile.rawAuthor] ?? profile;
            return (
              <div className="profile-row" key={profile.rawAuthor}>
                <span title={profile.rawAuthor}>{profile.rawAuthor}</span>
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
                <button className="primary-outline-button" onClick={() => void saveProfile(profile.rawAuthor)} disabled={saving === profile.rawAuthor}>
                  Save
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function ReportsTable({ reports }: { reports: Report[] }) {
  return (
    <section className="panel table-panel">
      <h2>Report Rows</h2>
      <div className="table">
        <div className="table-head">
          <span>Source</span>
          <span>Author</span>
          <span>Date</span>
          <span>Active</span>
          <span>Idle</span>
          <span>Recorded</span>
          <span>Timezone</span>
        </div>
        {reports.map((report, index) => (
          <div className="table-row" key={`${report.recordedAt ?? "report"}-${index}`}>
            <span>{formatSource(report.source)}</span>
            <span>{report.displayName ?? report.author ?? "Unknown User"}</span>
            <span>{report.date ?? "-"}</span>
            <span>{formatDuration(report.activeDeltaSeconds ?? 0)}</span>
            <span>{formatDuration(report.idleDeltaSeconds ?? 0)}</span>
            <span>{formatAuthorTime(report)}</span>
            <span>{formatTimeZoneLabel(report) ?? "-"}</span>
          </div>
        ))}
      </div>
    </section>
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

function DateRangePicker({ value, onChange }: { value: DateRange; onChange: (range: DateRange) => void }) {
  return (
    <div className="date-range-group">
      <div className="date-presets" aria-label="Date presets">
        <button onClick={() => onChange(todayRange())}>Today</button>
        <button onClick={() => onChange(currentWeekRange())}>Week</button>
        <button onClick={() => onChange(currentMonthRange())}>Month</button>
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

function Duration({ label, seconds }: { label: string; seconds: number }) {
  return (
    <div className="duration">
      <span>{label}</span>
      <strong>{formatDuration(seconds)}</strong>
    </div>
  );
}

function formatDuration(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
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

function breakClassName(seconds: number) {
  return seconds > 3600 ? "metric-value bad" : "metric-value";
}

function formatSource(source?: string) {
  if (source === "ual") {
    return "Unity";
  }

  return source ?? "-";
}

function formatActivityType(type: string) {
  const labels: Record<string, string> = {
    external: "External",
    play_mode: "Play Mode",
    prefab_saved: "Prefab Save",
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

  try {
    return new Intl.DateTimeFormat(undefined, {
      timeStyle: "short",
      timeZone: report.timeZoneId
    }).format(new Date(report.recordedAt));
  } catch {
    return formatTimestamp(report.recordedAt);
  }
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

  return [author.displayName, author.rawAuthor, author.team, author.telegramUsername].some((value) => value?.toLowerCase().includes(query));
}

function initials(value: string) {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function pageTitle(page: Page) {
  if (page === "activity") {
    return "Activity";
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

  if (page === "settings") {
    return "Manage author display names, teams, Telegram mapping, and report intervals.";
  }

  return "Team activity overview for the selected period.";
}

function todayRange(): DateRange {
  const today = toDateInputValue(new Date());
  return { startDate: today, endDate: today };
}

function currentWeekRange(): DateRange {
  const now = new Date();
  const day = now.getDay() || 7;
  const start = new Date(now);
  start.setDate(now.getDate() - day + 1);
  const end = new Date(start);
  end.setDate(start.getDate() + 6);
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
