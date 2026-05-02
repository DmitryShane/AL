import React, { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Box, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { AuthorsTable } from "../components/AuthorsTable";
import { AnalyticsActivityOverview } from "../components/AnalyticsActivityOverview";
import { HourlyActivityChart } from "../components/HourlyActivityChart";
import { ActivityCard } from "../components/activity/ActivityCard";
import { ActivityMetricsGrid } from "../components/activity/ActivityMetricsGrid";
import { BreakdownPanel, OvertimeBreakdownPanel } from "../components/activity/BreakdownPanels";
import { ReportsTable } from "../components/activity/ReportsTable";
import { apiFetch } from "../api/client";
import { MEETING_AUDIO_RETENTION_OPTIONS, MEETING_SUMMARY_LANGUAGES, REFRESH_INTERVAL_MS, REPORTS_PAGE_STORAGE_KEY, SETTINGS_TAB_STORAGE_KEY } from "../constants/dashboard";
import type { ActivitySummary, AlertStats, AnalyticsSummary, AuthorAlert, AuthorProfile, AuthorRow, CalendarAuthor, CalendarAuthorStats, CalendarMark, CalendarReason, CalendarSummary, DateRange, MeetingRecordingStatus, Report, ReportsPage, ReportsPageCache, SavedPrefab, SettingsTab, SiteUser, SiteUserRole, Summary } from "../types/dashboard";
import { activityColor, alertAuthorCardClassName, alertCardClassName, alertCountBadgeClassName, alertKey, alertSeverityBadgeClassName, authorCardClassName, authorCardProductivityTone, authorStatusBadgeClassName, autoBreakScheduleLabel, avatarStyle, breakClassName, breakTone, calendarDayClassName, compareAlertAuthors, compareAuthorCardStatus, compareAuthorsByStatusAndProductivity, dateRangeList, emptyAuthorProfile, formatActivityType, formatAlertThreshold, formatAlertValue, formatAuthorStatus, formatAuthorTime, formatDelta, formatDiscordEvent, formatDuration, formatDurationDelta, formatMinutes, formatProfileTimeZoneLabel, formatProfileTimeZoneTitle, formatReportActive, formatReportIdle, formatReportOvertime, formatReportType, formatSiteRole, formatSource, formatTelegramEvent, formatTimeZoneLabel, formatTimestamp, initials, loadSavedReportsPage, loadSavedSettingsTab, meetingRecordingAudioStats, meetingRecordingDetail, meetingRecordingRecipientLabel, meetingRecordingStatusLabel, monthIndexes, normalizeAuthorInput, paletteColor, productivityClassName, productivityTone, reportTypeBadgeClassName, savedFileLabel, settingsSaveButtonClassName, settingsSaveButtonLabel, sourceIcon, toCalendarDate, toDateInputValue, uniqueDates, authorProfilePayload } from "./pageHelpers";
export function ActivityPage({
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
  const [reportsPage, setReportsPageState] = useState(() => loadSavedReportsPage());
  const [reportSourceFilter, setReportSourceFilter] = useState("");
  const [reportsPageCache, setReportsPageCache] = useState<ReportsPageCache>({});
  const reportsResetKeyRef = useRef<string | null>(null);
  const reportsResetKey = useMemo(() => JSON.stringify({
    author: author?.rawAuthor ?? "",
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    dateMode: dateRange.preset === "live" ? "authorLocalToday" : "",
    source: reportSourceFilter,
    limit: reportsPageSize
  }), [author?.rawAuthor, dateRange.startDate, dateRange.endDate, dateRange.preset, reportSourceFilter, reportsPageSize]);
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
    if (!author?.rawAuthor) {
      return;
    }

    if (reportsResetKeyRef.current === null) {
      reportsResetKeyRef.current = reportsResetKey;
      return;
    }

    if (reportsResetKeyRef.current !== reportsResetKey) {
      reportsResetKeyRef.current = reportsResetKey;
      setReportsPage(1);
    }
  }, [author?.rawAuthor, reportsResetKey]);

  function setReportsPage(page: number | ((value: number) => number)) {
    setReportsPageState((current) => {
      const nextPage = typeof page === "function" ? page(current) : page;
      const normalizedPage = Math.max(1, nextPage);
      localStorage.setItem(REPORTS_PAGE_STORAGE_KEY, String(normalizedPage));
      return normalizedPage;
    });
  }

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
          <ActivityCard
            key={item.rawAuthor}
            author={item}
            active={item.rawAuthor === author?.rawAuthor}
            onSelect={(selected) => setSelectedAuthor(selected.rawAuthor)}
          />
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

          <ActivityMetricsGrid author={author} />

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

