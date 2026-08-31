import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Coffee, RefreshCw } from "lucide-react";
import { AuthorsTable } from "../components/AuthorsTable";
import { HourlyActivityChart } from "../components/HourlyActivityChart";
import { ActivityBreakdownCards } from "../components/activity/ActivityBreakdownCards";
import { ActivityCard } from "../components/activity/ActivityCard";
import { ActivityAuthorMiniCard } from "../components/activity/ActivityAuthorMiniCard";
import { ActivityMetricsGrid } from "../components/activity/ActivityMetricsGrid";
import { ReportsTable } from "../components/activity/ReportsTable";
import { DateRangePicker } from "../components/layout/DateRangePicker";
import { apiFetch } from "../api/client";
import { PAGE_SCROLL_STORAGE_PREFIX, REPORTS_PAGE_STORAGE_KEY } from "../constants/dashboard";
import type {
  ActivityHourlyDisplayFreshness,
  ActivityHourlyFreshness,
  ActivitySummary,
  AuthorHourlyActivity,
  AuthorRow,
  DateRange,
  Report,
  ReportsPage,
  ReportsPageCache
} from "../types/dashboard";
import { localBrowserStorage, readStorageItem, sessionBrowserStorage, writeStorageCache, writeStorageState } from "../utils/browserStorage";
import { compareAuthorCardStatus, loadSavedReportsPage } from "./pageHelpers";

const ACTIVITY_HOURLY_CACHE_PREFIX = "AL.Dashboard.ActivityHourly.";
const ACTIVITY_REPORTS_CACHE_PREFIX = "AL.Dashboard.ActivityReports.v2.";
const ACTIVITY_HOURLY_STATUS_POLL_MS = 3000;

type ActivityHourlyResponse = {
  hourlyActivityByAuthor: AuthorHourlyActivity[];
  freshness?: ActivityHourlyFreshness;
};

export function ActivityPage({
  summary,
  dateRange,
  datePickerValue,
  onDatePickerChange,
  selectedAuthor,
  authorSelectionError,
  setSelectedAuthor,
  loading,
  refreshing,
  onRefreshAuthor
}: {
  summary: ActivitySummary;
  dateRange: DateRange;
  datePickerValue: DateRange;
  onDatePickerChange: (range: DateRange) => void;
  selectedAuthor: string | null;
  authorSelectionError: string | null;
  setSelectedAuthor: (value: string) => void;
  loading: boolean;
  refreshing: boolean;
  onRefreshAuthor: (author: string) => void;
}) {
  const author = selectedAuthor ? summary.authors.find((item) => item.rawAuthor === selectedAuthor) ?? null : null;
  const snapshotPreparing = summary.snapshot?.status === "preparing" && summary.authors.length === 0;
  const snapshotEmpty = summary.snapshot?.status === "empty";
  const authorSnapshotPreparing = author?.snapshotStatus === "preparing" || author?.snapshotStatus === "live";
  const isHistoricalSingleDay = dateRange.preset !== "live" && dateRange.startDate === dateRange.endDate;
  const hourlyCacheKey = useMemo(() => JSON.stringify({
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    dateMode: dateRange.preset === "live" ? "authorLocalToday" : ""
  }), [dateRange.startDate, dateRange.endDate, dateRange.preset]);
  const [hourlyRows, setHourlyRows] = useState<AuthorHourlyActivity[]>(() => loadCachedActivityHourly(hourlyCacheKey) ?? summary.hourlyActivityByAuthor);
  const hourlyCacheRef = useRef<Record<string, AuthorHourlyActivity[]>>({});
  const [hourlyFreshness, setHourlyFreshness] = useState<ActivityHourlyDisplayFreshness | null>(null);
  const hourlyDataVersionRef = useRef<string | null>(null);
  const hourlyDataLoadedRef = useRef(false);
  const hourlyLoadRequestIdRef = useRef(0);
  const hourlyLoadInFlightKeyRef = useRef<string | null>(null);
  const hourlyRequestKey = `${hourlyCacheKey}:${author?.rawAuthor ?? ""}`;
  const authorHourly = useMemo(() => {
    const hourlySource = hourlyRows.length ? hourlyRows : summary.hourlyActivityByAuthor;
    const hourly = hourlySource
      .filter((item) => item.rawAuthor === author?.rawAuthor)
      .map((item) => ({ ...item, status: author?.status }));

    if (hourly.length || !author) {
      return hourly;
    }

    return [{ author: author.displayName, rawAuthor: author.rawAuthor, status: author.status, hourlyActivity: [] }];
  }, [author, hourlyRows, summary.hourlyActivityByAuthor]);
  const cardAuthors = useMemo(
    () => [...summary.authors].sort((left, right) => compareAuthorCardStatus(left, right, dateRange)),
    [summary.authors, dateRange]
  );
  const lastFloatingAuthorsRef = useRef<AuthorRow[]>(cardAuthors);

  if (!loading && summary.authors.length === 0) {
    lastFloatingAuthorsRef.current = [];
  }

  if (cardAuthors.length > 0) {
    lastFloatingAuthorsRef.current = cardAuthors;
  }

  const floatingAuthors = cardAuthors.length > 0 ? cardAuthors : lastFloatingAuthorsRef.current;
  const authorCardStripRef = useRef<HTMLDivElement>(null);
  const [showFloatingAuthorStrip, setShowFloatingAuthorStrip] = useState(false);
  const [reports, setReports] = useState<Report[]>([]);
  const [reportsTotal, setReportsTotal] = useState(0);
  const [reportSources, setReportSources] = useState<string[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [reportsError, setReportsError] = useState<string | null>(null);
  const [reportsPageSize, setReportsPageSize] = useState(10);
  const [reportsPage, setReportsPageState] = useState(() => loadSavedReportsPage());
  const [reportSourceFilter, setReportSourceFilter] = useState("");
  const [reportHourFilter, setReportHourFilter] = useState("");
  const reportsPageCacheRef = useRef<ReportsPageCache>({});
  const reportsResetKeyRef = useRef<string | null>(null);
  const reportsFreshnessKey = `${author?.lastReceivedAt ?? ""}:${author?.lastRecordedAt ?? ""}`;
  const reportsResetKey = useMemo(() => JSON.stringify({
    author: author?.rawAuthor ?? "",
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    dateMode: dateRange.preset === "live" ? "authorLocalToday" : "",
    source: reportSourceFilter,
    hour: reportHourFilter,
    limit: reportsPageSize,
    freshness: reportsFreshnessKey
  }), [author?.rawAuthor, dateRange.startDate, dateRange.endDate, dateRange.preset, reportSourceFilter, reportHourFilter, reportsPageSize, reportsFreshnessKey]);
  const reportsCacheKey = useMemo(() => JSON.stringify({
    author: author?.rawAuthor ?? "",
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    dateMode: dateRange.preset === "live" ? "authorLocalToday" : "",
    source: reportSourceFilter,
    hour: reportHourFilter,
    limit: reportsPageSize,
    page: reportsPage,
    freshness: reportsFreshnessKey
  }), [author?.rawAuthor, dateRange.startDate, dateRange.endDate, dateRange.preset, reportSourceFilter, reportHourFilter, reportsPageSize, reportsPage, reportsFreshnessKey]);
  useLayoutEffect(() => {
    let settleAttempts = 0;
    let pendingFrame: number | null = null;

    function readSavedActivityScrollY() {
      try {
        const raw = readStorageItem(sessionBrowserStorage(), `${PAGE_SCROLL_STORAGE_PREFIX}activity`);

        if (!raw) {
          return 0;
        }

        const value = Number(raw);

        if (!Number.isFinite(value)) {
          return 0;
        }

        return Math.max(0, value);
      } catch {
        return 0;
      }
    }

    function updateFloatingAuthorStrip() {
      const element = authorCardStripRef.current;

      if (!element || floatingAuthors.length === 0) {
        setShowFloatingAuthorStrip(false);
        settleAttempts = 0;
        return;
      }

      const savedScrollY = readSavedActivityScrollY();

      if (savedScrollY > 0 && window.scrollY + 1 < savedScrollY && settleAttempts < 12) {
        settleAttempts += 1;

        if (pendingFrame !== null) {
          window.cancelAnimationFrame(pendingFrame);
        }

        pendingFrame = window.requestAnimationFrame(() => {
          pendingFrame = null;
          updateFloatingAuthorStrip();
        });

        return;
      }

      settleAttempts = 0;
      setShowFloatingAuthorStrip(element.getBoundingClientRect().bottom <= 0);
    }

    updateFloatingAuthorStrip();
    window.addEventListener("scroll", updateFloatingAuthorStrip, { passive: true });
    window.addEventListener("resize", updateFloatingAuthorStrip);

    return () => {
      if (pendingFrame !== null) {
        window.cancelAnimationFrame(pendingFrame);
      }

      window.removeEventListener("scroll", updateFloatingAuthorStrip);
      window.removeEventListener("resize", updateFloatingAuthorStrip);
    };
  }, [cardAuthors.length, floatingAuthors.length, loading, summary.authors.length]);

  useEffect(() => {
    hourlyLoadRequestIdRef.current += 1;
    hourlyDataVersionRef.current = null;
    hourlyDataLoadedRef.current = false;
    setHourlyFreshness(
      dateRange.preset === "live" && author
        ? { status: "checking", dataThrough: null, timeZoneId: author.timeZoneId }
        : null
    );
  }, [author?.rawAuthor, dateRange.preset, hourlyRequestKey]);

  const loadHourly = useCallback(async (useCachedRows: boolean) => {
    if (isHistoricalSingleDay && (snapshotPreparing || snapshotEmpty || summary.hourlyActivityByAuthor.length)) {
      setHourlyRows(summary.hourlyActivityByAuthor);
      return;
    }

    if (loading && !summary.hourlyActivityByAuthor.length) {
      return;
    }

    const requestKey = hourlyRequestKey;

    if (hourlyLoadInFlightKeyRef.current === requestKey) {
      return;
    }

    let hasCachedRows = false;

    if (useCachedRows && !hourlyDataLoadedRef.current) {
      const cachedRows = hourlyCacheRef.current[hourlyCacheKey];

      if (cachedRows) {
        setHourlyRows(cachedRows);
        hasCachedRows = true;
      }

      const persistedRows = loadCachedActivityHourly(hourlyCacheKey);

      if (!cachedRows && persistedRows) {
        hourlyCacheRef.current = {
          ...hourlyCacheRef.current,
          [hourlyCacheKey]: persistedRows
        };
        setHourlyRows(persistedRows);
        hasCachedRows = true;
      }
    }

    const params = new URLSearchParams({
      startDate: dateRange.startDate,
      endDate: dateRange.endDate
    });

    if (dateRange.preset === "live") {
      params.set("dateMode", "authorLocalToday");
      if (author?.rawAuthor) {
        params.set("author", author.rawAuthor);
      }
    }

    const requestId = ++hourlyLoadRequestIdRef.current;
    hourlyLoadInFlightKeyRef.current = requestKey;

    try {
      const response = await apiFetch(`/api/v1/reports/activity-hourly?${params.toString()}`);

      if (!response.ok) {
        throw new Error("Hourly activity request failed");
      }

      const payload = (await response.json()) as ActivityHourlyResponse;

      if (requestId !== hourlyLoadRequestIdRef.current || requestKey !== hourlyRequestKey) {
        return;
      }

      hourlyCacheRef.current = {
        ...hourlyCacheRef.current,
        [hourlyCacheKey]: payload.hourlyActivityByAuthor
      };
      saveCachedActivityHourly(hourlyCacheKey, payload.hourlyActivityByAuthor);
      setHourlyRows(payload.hourlyActivityByAuthor);

      if (dateRange.preset === "live" && author) {
        hourlyDataLoadedRef.current = true;
        hourlyDataVersionRef.current = payload.freshness?.dataVersion ?? null;
        setHourlyFreshness({
          status: payload.freshness?.status ?? "unavailable",
          dataThrough: payload.freshness?.dataThrough ?? null,
          timeZoneId: author.timeZoneId
        });
      }
    } catch {
      if (requestId !== hourlyLoadRequestIdRef.current || requestKey !== hourlyRequestKey) {
        return;
      }

      if (summary.hourlyActivityByAuthor.length || !hasCachedRows) {
        setHourlyRows(summary.hourlyActivityByAuthor);
      }

      if (dateRange.preset === "live" && author) {
        setHourlyFreshness((current) => ({
          status: "delayed",
          dataThrough: current?.dataThrough ?? null,
          timeZoneId: author.timeZoneId
        }));
      }
    } finally {
      if (hourlyLoadInFlightKeyRef.current === requestKey) {
        hourlyLoadInFlightKeyRef.current = null;
      }
    }
  }, [author, dateRange.endDate, dateRange.preset, dateRange.startDate, hourlyCacheKey, hourlyRequestKey, isHistoricalSingleDay, loading, snapshotEmpty, snapshotPreparing, summary.hourlyActivityByAuthor]);

  useEffect(() => {
    void loadHourly(true);
  }, [loadHourly]);

  useEffect(() => {
    if (dateRange.preset !== "live" || !author?.rawAuthor) {
      return;
    }

    const selectedAuthor = author;
    let ignore = false;
    let statusRequestInFlight = false;

    async function checkHourlyStatus() {
      if (statusRequestInFlight) {
        return;
      }

      statusRequestInFlight = true;

      try {
        const params = new URLSearchParams({ author: selectedAuthor.rawAuthor });
        const response = await apiFetch(`/api/v1/reports/activity-hourly/status?${params.toString()}`);

        if (!response.ok) {
          throw new Error("Hourly activity status request failed");
        }

        const freshness = (await response.json()) as ActivityHourlyFreshness;

        if (ignore) {
          return;
        }

        if (freshness.status === "delayed") {
          setHourlyFreshness((current) => ({
            status: "delayed",
            dataThrough: current?.dataThrough ?? null,
            timeZoneId: selectedAuthor.timeZoneId
          }));
          return;
        }

        const dataVersionChanged = freshness.dataVersion !== hourlyDataVersionRef.current;

        if (freshness.status === "updating" || dataVersionChanged) {
          setHourlyFreshness((current) => ({
            status: "updating",
            dataThrough: current?.dataThrough ?? null,
            timeZoneId: selectedAuthor.timeZoneId
          }));

          if (freshness.status === "current" && dataVersionChanged) {
            void loadHourly(false);
          }
          return;
        }

        if (!hourlyDataLoadedRef.current) {
          setHourlyFreshness((current) => ({
            status: "checking",
            dataThrough: current?.dataThrough ?? null,
            timeZoneId: selectedAuthor.timeZoneId
          }));
          void loadHourly(false);
          return;
        }

        setHourlyFreshness((current) => ({
          status: "current",
          dataThrough: current?.dataThrough ?? freshness.dataThrough,
          timeZoneId: selectedAuthor.timeZoneId
        }));
      } catch {
        if (!ignore) {
          setHourlyFreshness((current) => ({
            status: "unavailable",
            dataThrough: current?.dataThrough ?? null,
            timeZoneId: selectedAuthor.timeZoneId
          }));
        }
      } finally {
        statusRequestInFlight = false;
      }
    }

    void checkHourlyStatus();
    const intervalId = window.setInterval(() => void checkHourlyStatus(), ACTIVITY_HOURLY_STATUS_POLL_MS);

    return () => {
      ignore = true;
      window.clearInterval(intervalId);
    };
  }, [author?.rawAuthor, author?.timeZoneId, dateRange.preset, loadHourly]);

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
      writeStorageState(localBrowserStorage(), REPORTS_PAGE_STORAGE_KEY, String(normalizedPage));
      return normalizedPage;
    });
  }

  useEffect(() => {
    let ignore = false;

    async function loadReports() {
      if (snapshotEmpty) {
        setReports([]);
        setReportsTotal(0);
        setReportSources([]);
        setReportsLoading(false);
        setReportsError(null);
        return;
      }

      if (authorSnapshotPreparing) {
        setReports([]);
        setReportsTotal(0);
        setReportSources([]);
        setReportsLoading(false);
        setReportsError(null);
        return;
      }

      if (!author?.rawAuthor) {
        if (loading) {
          return;
        }

        setReports([]);
        setReportsTotal(0);
        setReportSources([]);
        return;
      }

      const cachedPage = reportsPageCacheRef.current[reportsCacheKey];

      if (cachedPage) {
        setReports(cachedPage.reports);
        setReportsTotal(cachedPage.total);
        setReportSources(cachedPage.sources);
        setReportsLoading(false);
        setReportsError(null);
        return;
      }

      const persistedPage = loadCachedReportsPage(reportsCacheKey);

      if (persistedPage) {
        setReports(persistedPage.reports);
        setReportsTotal(persistedPage.total);
        setReportSources(persistedPage.sources);
        reportsPageCacheRef.current = {
          ...reportsPageCacheRef.current,
          [reportsCacheKey]: persistedPage
        };
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

      if (reportHourFilter) {
        params.set("hour", reportHourFilter);
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
        reportsPageCacheRef.current = {
          ...reportsPageCacheRef.current,
          [reportsCacheKey]: payload
        };
        saveCachedReportsPage(reportsCacheKey, payload);
      } catch (requestError) {
        if (ignore) {
          return;
        }

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
  }, [author?.rawAuthor, authorSnapshotPreparing, dateRange.startDate, dateRange.endDate, dateRange.preset, loading, reportsPage, reportsPageSize, reportSourceFilter, reportHourFilter, reportsCacheKey, snapshotEmpty]);

  return (
    <>
      <div
        className={`activity-author-floating-strip${showFloatingAuthorStrip ? " is-visible" : ""}`}
        data-doc-target="activity-floating-author-strip"
        role="region"
        aria-label="Authors and date range"
        aria-hidden={!showFloatingAuthorStrip}
      >
        <div className="activity-author-floating-strip-inner">
          <div className="activity-author-floating-strip-scroll">
            {floatingAuthors.map((item) => (
              <ActivityAuthorMiniCard
                key={`float-${item.rawAuthor}`}
                author={item}
                active={item.rawAuthor === author?.rawAuthor}
                onSelect={(selected) => setSelectedAuthor(selected.rawAuthor)}
              />
            ))}
          </div>
          <div className="activity-author-floating-strip-dates">
            <DateRangePicker value={datePickerValue} onChange={onDatePickerChange} />
          </div>
        </div>
      </div>
      <section className="page-section" data-doc-target="activity-overview" id="activity-overview">
        <div ref={authorCardStripRef} className="author-card-strip" data-doc-target="activity-author-cards" id="activity-author-cards">
          {cardAuthors.map((item) => (
            <ActivityCard
              key={item.rawAuthor}
              author={item}
              active={item.rawAuthor === author?.rawAuthor}
              onSelect={(selected) => setSelectedAuthor(selected.rawAuthor)}
            />
          ))}
        </div>

        {snapshotPreparing ? (
          <p className="empty" data-doc-target="activity-snapshot-preparing">Preparing historical activity snapshot for {summary.snapshot?.date ?? dateRange.startDate}...</p>
        ) : snapshotEmpty ? (
          <div className="activity-empty-day-state" data-doc-target="activity-empty-day">
            <div className="activity-empty-day-illustration" aria-hidden="true">
              <Coffee size={42} strokeWidth={1.8} />
            </div>
            <strong>No activity data for this day</strong>
            <p>This was a day off, so nobody worked and no activity reports were recorded.</p>
          </div>
        ) : authorSnapshotPreparing ? (
          <p className="empty" data-doc-target="activity-snapshot-preparing">
            {author?.snapshotStatus === "live"
              ? `Historical activity snapshot for ${author.displayName} will be prepared after their local day ends.`
              : `Preparing historical activity snapshot for ${author?.displayName ?? "this author"}...`}
          </p>
        ) : author ? (
          <>
            <div className="toolbar" data-doc-target="activity-selected-author" id="activity-selected-author">
              <div>
                <strong>{author.displayName}</strong>
                <p className="toolbar-caption">Request a fresh Unity report for this author.</p>
              </div>
              <div className="toolbar-spacer" />
              <button className="primary-outline-button" data-doc-target="activity-refresh-author" onClick={() => onRefreshAuthor(author.rawAuthor)} disabled={refreshing}>
                <RefreshCw size={16} />
                {refreshing ? "Requesting..." : "Refresh"}
              </button>
            </div>

            <AuthorsTable authors={[author]} emptyMessage="No selected author activity for this period." />

            <ActivityMetricsGrid author={author} />

            <div className="dashboard-insights-row">
              <HourlyActivityChart authors={authorHourly} freshness={hourlyFreshness} />
              <ActivityBreakdownCards author={author} />
            </div>

            <ReportsTable
              reports={reports}
              total={reportsTotal}
              page={reportsPage}
              pageSize={reportsPageSize}
              sourceFilter={reportSourceFilter}
              sourceOptions={reportSources}
              hourFilter={reportHourFilter}
              loading={reportsLoading}
              error={reportsError}
              setPage={setReportsPage}
              setPageSize={setReportsPageSize}
              setSourceFilter={setReportSourceFilter}
              setHourFilter={setReportHourFilter}
            />
          </>
        ) : authorSelectionError ? (
          <p className="empty">{authorSelectionError}</p>
        ) : selectedAuthor ? (
          <p className="empty">Selected author is not available in the current activity data.</p>
        ) : loading ? null : (
          <p className="empty">Select an author.</p>
        )}
    </section>
    </>
  );
}

function activityCacheKey(prefix: string, key: string) {
  return `${prefix}${key}`;
}

function loadCachedActivityHourly(key: string) {
  const storageKey = activityCacheKey(ACTIVITY_HOURLY_CACHE_PREFIX, key);

  try {
    const cached = readStorageItem(localBrowserStorage(), storageKey) ?? readStorageItem(sessionBrowserStorage(), storageKey);

    if (!cached) {
      return null;
    }

    return JSON.parse(cached) as AuthorHourlyActivity[];
  } catch {
    return null;
  }
}

function saveCachedActivityHourly(key: string, rows: AuthorHourlyActivity[]) {
  writeStorageCache(localBrowserStorage(), activityCacheKey(ACTIVITY_HOURLY_CACHE_PREFIX, key), JSON.stringify(rows));
}

function loadCachedReportsPage(key: string) {
  const storageKey = activityCacheKey(ACTIVITY_REPORTS_CACHE_PREFIX, key);

  try {
    const cached = readStorageItem(localBrowserStorage(), storageKey) ?? readStorageItem(sessionBrowserStorage(), storageKey);

    if (!cached) {
      return null;
    }

    return JSON.parse(cached) as ReportsPage;
  } catch {
    return null;
  }
}

function saveCachedReportsPage(key: string, page: ReportsPage) {
  writeStorageCache(localBrowserStorage(), activityCacheKey(ACTIVITY_REPORTS_CACHE_PREFIX, key), JSON.stringify(page));
}
