import React, { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Box, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { AuthorsTable } from "../components/AuthorsTable";
import { AnalyticsActivityOverview } from "../components/AnalyticsActivityOverview";
import { HourlyActivityChart } from "../components/HourlyActivityChart";
import { ActivityMetricsGrid } from "../components/activity/ActivityMetricsGrid";
import { BreakdownPanel, OvertimeBreakdownPanel } from "../components/activity/BreakdownPanels";
import { ReportsTable } from "../components/activity/ReportsTable";
import { apiFetch } from "../api/client";
import { MEETING_AUDIO_RETENTION_OPTIONS, MEETING_SUMMARY_LANGUAGES, REFRESH_INTERVAL_MS, REPORTS_PAGE_STORAGE_KEY, SETTINGS_TAB_STORAGE_KEY } from "../constants/dashboard";
import type { ActivitySummary, AlertStats, AnalyticsSummary, AuthorAlert, AuthorProfile, AuthorRow, CalendarAuthor, CalendarAuthorStats, CalendarMark, CalendarReason, CalendarSummary, DateRange, MeetingRecordingStatus, Report, ReportsPage, ReportsPageCache, SavedPrefab, SettingsTab, SiteUser, SiteUserRole, Summary } from "../types/dashboard";
import { activityColor, alertAuthorCardClassName, alertCardClassName, alertCountBadgeClassName, alertKey, alertSeverityBadgeClassName, authorCardClassName, authorCardProductivityTone, authorStatusBadgeClassName, autoBreakScheduleLabel, avatarStyle, breakClassName, breakTone, calendarDayClassName, compareAlertAuthors, compareAuthorCardStatus, compareAuthorsByStatusAndProductivity, dateRangeList, emptyAuthorProfile, formatActivityType, formatAlertThreshold, formatAlertValue, formatAuthorStatus, formatAuthorTime, formatDelta, formatDiscordEvent, formatDuration, formatDurationDelta, formatMinutes, formatProfileTimeZoneLabel, formatProfileTimeZoneTitle, formatReportActive, formatReportIdle, formatReportOvertime, formatReportType, formatSiteRole, formatSource, formatTelegramEvent, formatTimeZoneLabel, formatTimestamp, initials, loadSavedReportsPage, loadSavedSettingsTab, meetingRecordingAudioStats, meetingRecordingDetail, meetingRecordingRecipientLabel, meetingRecordingStatusLabel, monthIndexes, normalizeAuthorInput, paletteColor, productivityClassName, productivityTone, reportTypeBadgeClassName, savedFileLabel, settingsSaveButtonClassName, settingsSaveButtonLabel, sourceIcon, toCalendarDate, toDateInputValue, uniqueDates, authorProfilePayload } from "./pageHelpers";
export function LoginPage({ checkingSession = false, onLogin }: { checkingSession?: boolean; onLogin: (user: SiteUser) => void }) {
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

