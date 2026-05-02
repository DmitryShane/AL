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
export function AuthorsPage({
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

