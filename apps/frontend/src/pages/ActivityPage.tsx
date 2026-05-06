import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { AuthorsTable } from "../components/AuthorsTable";
import { HourlyActivityChart } from "../components/HourlyActivityChart";
import { ActivityCard } from "../components/activity/ActivityCard";
import { ActivityAuthorMiniCard } from "../components/activity/ActivityAuthorMiniCard";
import { ActivityMetricsGrid } from "../components/activity/ActivityMetricsGrid";
import { BreakdownPanel, OvertimeBreakdownPanel, type BreakdownPanelItem } from "../components/activity/BreakdownPanels";
import { ReportsTable } from "../components/activity/ReportsTable";
import { DateRangePicker } from "../components/layout/DateRangePicker";
import { apiFetch } from "../api/client";
import { PAGE_SCROLL_STORAGE_PREFIX, REPORTS_PAGE_STORAGE_KEY } from "../constants/dashboard";
import type { ActivitySummary, AuthorHourlyActivity, AuthorRow, DateRange, Report, ReportsPage, ReportsPageCache, SavedPrefab } from "../types/dashboard";
import { formatSource } from "../utils/format";
import { activityColor, compareAuthorCardStatus, formatActivityType, loadSavedReportsPage, paletteColor, savedFileLabel } from "./pageHelpers";

const ACTIVITY_HOURLY_CACHE_PREFIX = "AL.Dashboard.ActivityHourly.";
const ACTIVITY_REPORTS_CACHE_PREFIX = "AL.Dashboard.ActivityReports.";

export function ActivityPage({
  summary,
  dateRange,
  datePickerValue,
  onDatePickerChange,
  selectedAuthor,
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
  setSelectedAuthor: (value: string) => void;
  loading: boolean;
  refreshing: boolean;
  onRefreshAuthor: (author: string) => void;
}) {
  const author = summary.authors.find((item) => item.rawAuthor === selectedAuthor) ?? summary.authors[0];
  const hourlyCacheKey = useMemo(() => JSON.stringify({
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    dateMode: dateRange.preset === "live" ? "authorLocalToday" : ""
  }), [dateRange.startDate, dateRange.endDate, dateRange.preset]);
  const [hourlyRows, setHourlyRows] = useState<AuthorHourlyActivity[]>(() => loadCachedActivityHourly(hourlyCacheKey) ?? summary.hourlyActivityByAuthor);
  const hourlyCacheRef = useRef<Record<string, AuthorHourlyActivity[]>>({});
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
  const activityMix = author?.activityMix ?? [];
  const savedPrefabs = author?.savedPrefabs ?? [];
  const overtimeActivityMix = author?.overtimeActivityMix ?? [];
  const overtimeSavedPrefabs = author?.overtimeSavedPrefabs ?? [];
  const activityMixItems = activityMix.map((item) => activityMixPanelItem(item.type, item.count, item.percent));
  const savedPrefabItems = savedPrefabs.map((prefab, index) => savedPrefabPanelItem(prefab, index));
  const overtimeActivityMixItems = overtimeActivityMix.map((item) => activityMixPanelItem(item.type, item.count, item.percent));
  const overtimeSavedPrefabItems = overtimeSavedPrefabs.map((prefab, index) => savedPrefabPanelItem(prefab, index));
  const activityMixGroups = (author?.activityMixBySource ?? []).map((group) => ({
    source: group.source,
    label: formatSource(group.source),
    totalDisplayValue: String(group.totalCount),
    items: group.activityMix.map((item) => activityMixPanelItem(item.type, item.count, item.percent, group.source))
  }));
  const savedPrefabGroups = (author?.savedPrefabsBySource ?? []).map((group) => ({
    source: group.source,
    label: formatSource(group.source),
    totalDisplayValue: String(group.totalSaveCount),
    items: group.savedPrefabs.map((prefab, index) => savedPrefabPanelItem(prefab, index, group.source))
  }));
  const overtimeActivityMixGroups = (author?.overtimeActivityMixBySource ?? []).map((group) => ({
    source: group.source,
    label: formatSource(group.source),
    totalDisplayValue: String(group.totalCount),
    items: group.activityMix.map((item) => activityMixPanelItem(item.type, item.count, item.percent, group.source))
  }));
  const overtimeSavedPrefabGroups = (author?.overtimeSavedPrefabsBySource ?? []).map((group) => ({
    source: group.source,
    label: formatSource(group.source),
    totalDisplayValue: String(group.totalSaveCount),
    items: group.savedPrefabs.map((prefab, index) => savedPrefabPanelItem(prefab, index, group.source))
  }));
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
  const reportsResetKey = useMemo(() => JSON.stringify({
    author: author?.rawAuthor ?? "",
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    dateMode: dateRange.preset === "live" ? "authorLocalToday" : "",
    source: reportSourceFilter,
    hour: reportHourFilter,
    limit: reportsPageSize
  }), [author?.rawAuthor, dateRange.startDate, dateRange.endDate, dateRange.preset, reportSourceFilter, reportHourFilter, reportsPageSize]);
  const reportsCacheKey = useMemo(() => JSON.stringify({
    author: author?.rawAuthor ?? "",
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    dateMode: dateRange.preset === "live" ? "authorLocalToday" : "",
    source: reportSourceFilter,
    hour: reportHourFilter,
    limit: reportsPageSize,
    page: reportsPage
  }), [author?.rawAuthor, dateRange.startDate, dateRange.endDate, dateRange.preset, reportSourceFilter, reportHourFilter, reportsPageSize, reportsPage]);
  useLayoutEffect(() => {
    let settleAttempts = 0;
    let pendingFrame: number | null = null;

    function readSavedActivityScrollY() {
      try {
        const raw = sessionStorage.getItem(`${PAGE_SCROLL_STORAGE_PREFIX}activity`);

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
    let ignore = false;

    async function loadHourly() {
      if (loading && !summary.hourlyActivityByAuthor.length) {
        return;
      }

      const cachedRows = hourlyCacheRef.current[hourlyCacheKey];
      let hasCachedRows = false;

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

      const params = new URLSearchParams({
        startDate: dateRange.startDate,
        endDate: dateRange.endDate
      });

      if (dateRange.preset === "live") {
        params.set("dateMode", "authorLocalToday");
      }

      try {
        const response = await apiFetch(`/api/v1/reports/activity-hourly?${params.toString()}`);

        if (!response.ok) {
          throw new Error("Hourly activity request failed");
        }

        const payload = (await response.json()) as { hourlyActivityByAuthor: AuthorHourlyActivity[] };

        if (ignore) {
          return;
        }

        hourlyCacheRef.current = {
          ...hourlyCacheRef.current,
          [hourlyCacheKey]: payload.hourlyActivityByAuthor
        };
        saveCachedActivityHourly(hourlyCacheKey, payload.hourlyActivityByAuthor);
        setHourlyRows(payload.hourlyActivityByAuthor);
      } catch {
        if (!ignore) {
          if (summary.hourlyActivityByAuthor.length || !hasCachedRows) {
            setHourlyRows(summary.hourlyActivityByAuthor);
          }
        }
      }
    }

    void loadHourly();

    return () => {
      ignore = true;
    };
  }, [dateRange.startDate, dateRange.endDate, dateRange.preset, hourlyCacheKey, loading, summary.hourlyActivityByAuthor]);

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
  }, [author?.rawAuthor, dateRange.startDate, dateRange.endDate, dateRange.preset, loading, reportsPage, reportsPageSize, reportSourceFilter, reportHourFilter, reportsCacheKey]);

  return (
    <>
      <div
        className={`activity-author-floating-strip${showFloatingAuthorStrip ? " is-visible" : ""}`}
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
      <section className="page-section">
        <div ref={authorCardStripRef} className="author-card-strip">
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
                items={activityMixItems}
                groups={activityMixGroups}
              />
              <BreakdownPanel
                key={`${author.rawAuthor}-saved-files`}
                title="Saved Files"
                items={savedPrefabItems}
                groups={savedPrefabGroups}
              />
              <OvertimeBreakdownPanel
                key={`${author.rawAuthor}-overtime`}
                activityItems={overtimeActivityMixItems}
                savedItems={overtimeSavedPrefabItems}
                activityGroups={overtimeActivityMixGroups}
                savedGroups={overtimeSavedPrefabGroups}
              />
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
        ) : (
          <p className="empty">No author activity for this period.</p>
        )}
    </section>
    </>
  );
}

function activityMixPanelItem(type: string, count: number, percent: number, source?: string): BreakdownPanelItem {
  const itemId = source ? `${source}:${type}` : type;

  return {
    id: itemId,
    label: formatActivityType(type),
    value: percent,
    displayValue: `${percent}%`,
    color: activityColor(type || String(count))
  };
}

function savedPrefabPanelItem(prefab: SavedPrefab, index: number, source?: string): BreakdownPanelItem {
  return {
    id: source ? `${source}:${prefab.path || prefab.name}-${index}` : prefab.path || `${prefab.name}-${index}`,
    label: savedFileLabel(prefab),
    value: prefab.saveCount,
    displayValue: String(prefab.saveCount),
    color: paletteColor(index)
  };
}

function activityCacheKey(prefix: string, key: string) {
  return `${prefix}${key}`;
}

function loadCachedActivityHourly(key: string) {
  const storageKey = activityCacheKey(ACTIVITY_HOURLY_CACHE_PREFIX, key);

  try {
    const cached = localStorage.getItem(storageKey) ?? sessionStorage.getItem(storageKey);

    if (!cached) {
      return null;
    }

    return JSON.parse(cached) as AuthorHourlyActivity[];
  } catch {
    return null;
  }
}

function saveCachedActivityHourly(key: string, rows: AuthorHourlyActivity[]) {
  try {
    localStorage.setItem(activityCacheKey(ACTIVITY_HOURLY_CACHE_PREFIX, key), JSON.stringify(rows));
  } catch {
    // Ignore storage failures; live API data is still shown.
  }
}

function loadCachedReportsPage(key: string) {
  const storageKey = activityCacheKey(ACTIVITY_REPORTS_CACHE_PREFIX, key);

  try {
    const cached = localStorage.getItem(storageKey) ?? sessionStorage.getItem(storageKey);

    if (!cached) {
      return null;
    }

    return JSON.parse(cached) as ReportsPage;
  } catch {
    return null;
  }
}

function saveCachedReportsPage(key: string, page: ReportsPage) {
  try {
    localStorage.setItem(activityCacheKey(ACTIVITY_REPORTS_CACHE_PREFIX, key), JSON.stringify(page));
  } catch {
    // Ignore storage failures; live API data is still shown.
  }
}

