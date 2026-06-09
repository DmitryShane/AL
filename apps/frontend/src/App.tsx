import { useMemo, useState } from "react";
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
import "./styles.css";

import { AuthorAvatar } from "./components/AuthorAvatar";
import { useActivityAuthorSelection } from "./hooks/useActivityAuthorSelection";
import { useAuthSession } from "./hooks/useAuthSession";
import { useDashboardData } from "./hooks/useDashboardData";
import { useDashboardNavigation } from "./hooks/useDashboardNavigation";
import { formatSiteRole, formatSiteUserSidebarLabel, shouldHideInactiveOfflineAuthor } from "./pages/pageHelpers";
import { emptyActivitySummary, loadSavedDateRange, pageUsesDashboardSummary } from "./utils/dashboardStorage";
import type { AuthorRow, Page } from "./types/dashboard";

function App() {
  const [dateRange, setDateRange] = useState(() => loadSavedDateRange());
  const [search, setSearch] = useState("");
  const {
    authUser,
    authLoading,
    hasAuthHint,
    sessionUserPreview,
    setAuthUser,
    clearAuthState,
    logout
  } = useAuthSession();
  const canShowCachedDashboard = Boolean(authUser) || (authLoading && hasAuthHint);
  const { page, selectPage: selectNavigationPage, isRestoringScroll } = useDashboardNavigation({
    canRestoreScroll: canShowCachedDashboard
  });
  const {
    appliedDateRange,
    summary,
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
  } = useDashboardData({
    page,
    dateRange,
    authUser,
    authLoading,
    clearAuthState
  });
  const { selectedAuthor, setSelectedAuthor } = useActivityAuthorSelection(page);

  const hasKnownPage = page !== null;
  const activitySummary = canShowCachedDashboard ? (summary?.activitySummary ?? emptyActivitySummary) : emptyActivitySummary;
  const cachedAuthorsActivitySummary = cachedAuthors.length
    ? { ...emptyActivitySummary, authors: cachedAuthors }
    : emptyActivitySummary;
  const activityDisplaySummary = canShowCachedDashboard
    ? (summary?.activitySummary ?? cachedActivitySummary ?? cachedAuthorsActivitySummary)
    : emptyActivitySummary;
  const visibleActivitySummary = useMemo(
    () => ({
      ...activityDisplaySummary,
      authors: activityDisplaySummary.authors.filter((author) => !shouldHideInactiveOfflineAuthor(author, new Date(), appliedDateRange))
    }),
    [activityDisplaySummary, appliedDateRange]
  );
  const settingsDisplaySummary = canShowCachedDashboard ? (summary ?? cachedSettingsSummary) : null;
  const isVisualLoading = canShowCachedDashboard && hasKnownPage && pageUsesDashboardSummary(page) && !summary && (loading || authLoading || !authUser);
  const hasDashboardDisplayData =
    page === "activity"
      ? Boolean(summary?.activitySummary ?? cachedActivitySummary ?? cachedAuthors.length)
      : page === "settings"
        ? Boolean(summary ?? cachedSettingsSummary)
        : Boolean(summary ?? cachedAuthors.length);
  const authorsSource = isVisualLoading && !activitySummary.authors.length ? cachedAuthors : activitySummary.authors;
  const authors = useMemo(
    () => authorsSource.filter((author) => !shouldHideInactiveOfflineAuthor(author) && matchesAuthorSearch(author, search)),
    [authorsSource, search]
  );
  const displaySessionUser = authUser ?? sessionUserPreview;
  const isDashboardLoading = canShowCachedDashboard && hasKnownPage && pageUsesDashboardSummary(page) && (loading || authLoading || !authUser);
  const showDashboardLoading = isDashboardLoading && !hasDashboardDisplayData;
  const backendStatusLabel =
    healthStatus === "online" ? "Backend online" : healthStatus === "offline" ? "Backend offline" : "Checking backend...";
  const backendStatusClassName = healthStatus === "online" ? "status-pill online" : `status-pill ${healthStatus}`;

  function selectPage(nextPage: Page) {
    selectNavigationPage(nextPage);
    showCachedSummaryForPage(nextPage);
  }

  async function handleLogout() {
    await logout();
    clearDashboardState();
  }

  if (authLoading && !hasAuthHint) {
    return <LoginPage checkingSession onLogin={setAuthUser} />;
  }

  if (!authLoading && !authUser && !hasAuthHint) {
    return <LoginPage onLogin={setAuthUser} />;
  }

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
          {displaySessionUser ? (
            <>
              <div className="session-card-figure" aria-hidden="true">
                <AuthorAvatar
                  displayName={formatSiteUserSidebarLabel(displaySessionUser)}
                  avatarUrl={displaySessionUser.avatarUrl}
                  variant="mini"
                  className="session-card-avatar-figure"
                />
              </div>
              <div className="session-card-text">
                <span>{formatSiteUserSidebarLabel(displaySessionUser)}</span>
                <small>{formatSiteRole(displaySessionUser.role)}</small>
              </div>
            </>
          ) : (
            <>
              <div className="session-card-figure" aria-hidden="true">
                <span className="session-card-avatar session-card-avatar-pending">…</span>
              </div>
              <div className="session-card-text">
                <span className="session-card-restoring">Loading account…</span>
                <small className="session-card-restoring-role" aria-hidden="true">
                  {"\u00a0"}
                </small>
              </div>
            </>
          )}
          <button
            className="icon-button"
            type="button"
            onClick={() => void handleLogout()}
            title="Log out"
            disabled={!displaySessionUser}
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-topbar">
          <div className="topbar-title-block">
            <h1>{pageTitle(page)}</h1>
            {!authLoading && showDashboardLoading ? <span className="topbar-loading-popover">Loading dashboard data...</span> : null}
            <p>{pageSubtitle(page)}</p>
          </div>
          {page === "authors" || page === "activity" ? (
            <div className="topbar-actions">
              <DateRangePicker value={dateRange} onChange={setDateRange} />
            </div>
          ) : page === "settings" ? (
            <div className="topbar-actions">
              <span className={backendStatusClassName}>
                <span className="status-pill-dot" aria-hidden="true" />
                {backendStatusLabel}
              </span>
            </div>
          ) : null}
        </header>

        {error ? <p className="notice error">{error}</p> : null}

        {page === "authors" ? (
          <AuthorsPage
            authors={authors}
            loading={isVisualLoading}
            search={search}
            setSearch={setSearch}
          />
        ) : null}
        {page === "activity" ? (
          <ActivityPage
            summary={visibleActivitySummary}
            dateRange={appliedDateRange}
            datePickerValue={dateRange}
            onDatePickerChange={setDateRange}
            selectedAuthor={selectedAuthor}
            setSelectedAuthor={setSelectedAuthor}
            loading={isVisualLoading}
            refreshing={refreshingReports}
            onRefreshAuthor={(author) => void requestReportRefresh(author)}
          />
        ) : null}
        {page === "analytics" ? <AnalyticsPage /> : null}
        {page === "calendar" ? <CalendarPage /> : null}
        {page === "alerts" ? <AlertsPage /> : null}
        {page === "settings" && displaySessionUser ? (
          <SettingsPage summary={settingsDisplaySummary} currentUser={displaySessionUser} onSaved={() => void load(false)} />
        ) : null}
        {!page ? <p className="empty">Page not found.</p> : null}
      </main>
    </div>
  );
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

function pageTitle(page: Page | null) {
  if (!page) {
    return "Not found";
  }

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

function pageSubtitle(page: Page | null) {
  if (!page) {
    return "";
  }

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
    return "";
  }

  if (page === "settings") {
    return "Manage workspace configuration, integrations, and dashboard behavior.";
  }

  return "Team activity overview for the selected period.";
}

export default App;
