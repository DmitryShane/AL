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
export function AnalyticsPage() {
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

