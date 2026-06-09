import {
  AUTH_HINT_STORAGE_KEY,
  DATE_RANGE_STORAGE_KEY,
  DASHBOARD_SUMMARY_CACHE_PREFIX,
  PAGE_SCROLL_STORAGE_PREFIX,
  SESSION_USER_PREVIEW_STORAGE_KEY
} from "../constants/dashboard";
import type { ActivitySummary, AuthorRow, DateRange, Page, SiteUser, SiteUserRole, Summary } from "../types/dashboard";

const LAST_AUTHORS_CACHE_PREFIX = "AL.Dashboard.LastAuthors.";
const ACTIVITY_SUMMARY_CACHE_PREFIX = "AL.Dashboard.ActivitySummary.";
const SETTINGS_SUMMARY_CACHE_KEY = "AL.Dashboard.SettingsSummary";

export const emptyActivitySummary: ActivitySummary = {
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

export function readStoredSessionUserPreview(): SiteUser | null {
  if (typeof window === "undefined" || localStorage.getItem(AUTH_HINT_STORAGE_KEY) !== "true") {
    return null;
  }

  try {
    const raw = sessionStorage.getItem(SESSION_USER_PREVIEW_STORAGE_KEY);

    if (!raw) {
      return null;
    }

    const data = JSON.parse(raw) as Partial<SiteUser>;

    if (typeof data.email !== "string" || typeof data.displayName !== "string" || typeof data.role !== "string") {
      return null;
    }

    const avatarUrl = typeof data.avatarUrl === "string" && data.avatarUrl.trim() ? data.avatarUrl.trim() : undefined;

    return {
      email: data.email,
      displayName: data.displayName,
      role: data.role as SiteUserRole,
      canViewServerStats: data.canViewServerStats === true,
      active: data.active !== false,
      ...(avatarUrl ? { avatarUrl } : {})
    };
  } catch {
    return null;
  }
}

export function writeStoredSessionUserPreview(user: SiteUser | null) {
  if (typeof window === "undefined") {
    return;
  }

  if (!user) {
    sessionStorage.removeItem(SESSION_USER_PREVIEW_STORAGE_KEY);
    return;
  }

  try {
    const avatarUrl = typeof user.avatarUrl === "string" && user.avatarUrl.trim() ? user.avatarUrl.trim() : undefined;
    sessionStorage.setItem(
      SESSION_USER_PREVIEW_STORAGE_KEY,
      JSON.stringify({
        email: user.email,
        displayName: user.displayName,
        role: user.role,
        canViewServerStats: user.canViewServerStats,
        active: user.active,
        ...(avatarUrl ? { avatarUrl } : {})
      })
    );
  } catch {
    //
  }
}

function lastAuthorsCacheKey(dateRange: DateRange) {
  const dateMode = dateRange.preset === "live" ? "authorLocalToday" : "";
  return `${LAST_AUTHORS_CACHE_PREFIX}${dateRange.startDate}.${dateRange.endDate}.${dateMode}`;
}

export function readCachedAuthors(dateRange: DateRange): AuthorRow[] {
  try {
    const raw = localStorage.getItem(lastAuthorsCacheKey(dateRange));

    if (!raw) {
      return [];
    }

    const authors = JSON.parse(raw) as unknown;

    if (!Array.isArray(authors)) {
      return [];
    }

    return authors as AuthorRow[];
  } catch {
    return [];
  }
}

export function saveCachedAuthors(dateRange: DateRange, authors: AuthorRow[]) {
  try {
    localStorage.setItem(lastAuthorsCacheKey(dateRange), JSON.stringify(authors));
  } catch {
    // Browsers can reject storage writes in private mode or when the quota is full.
  }
}

function activitySummaryCacheKey(dateRange: DateRange) {
  const dateMode = dateRange.preset === "live" ? "authorLocalToday" : "";
  return `${ACTIVITY_SUMMARY_CACHE_PREFIX}${dateRange.startDate}.${dateRange.endDate}.${dateMode}`;
}

export function readCachedActivitySummary(dateRange: DateRange): ActivitySummary | null {
  try {
    const raw = localStorage.getItem(activitySummaryCacheKey(dateRange));

    if (!raw) {
      return null;
    }

    return sanitizeCachedActivitySummaryValue(JSON.parse(raw) as ActivitySummary);
  } catch {
    return null;
  }
}

export function saveCachedActivitySummary(dateRange: DateRange, summary: ActivitySummary) {
  try {
    const previous = readCachedActivitySummary(dateRange);

    if (previous?.authors.length && !summary.authors.length) {
      return;
    }

    localStorage.setItem(activitySummaryCacheKey(dateRange), JSON.stringify(summary));
  } catch {
    // Browsers can reject storage writes in private mode or when the quota is full.
  }
}

export function readCachedSettingsSummary(): Summary | null {
  try {
    const raw = localStorage.getItem(SETTINGS_SUMMARY_CACHE_KEY);

    if (!raw) {
      return null;
    }

    return sanitizeCachedDashboardSummary(JSON.parse(raw) as Summary);
  } catch {
    return null;
  }
}

export function saveCachedSettingsSummary(summary: Summary) {
  try {
    localStorage.setItem(SETTINGS_SUMMARY_CACHE_KEY, JSON.stringify(summary));
  } catch {
    // Browsers can reject storage writes in private mode or when the quota is full.
  }
}

export function summaryViewForPage(page: Page) {
  if (page === "settings") {
    return "settings";
  }

  if (page === "activity") {
    return "activity";
  }

  return "authors";
}

export function pageUsesDashboardSummary(page: Page) {
  return page === "authors" || page === "activity" || page === "settings";
}

export function dashboardSummaryCacheKey(page: Page, dateRange: DateRange) {
  const view = summaryViewForPage(page);
  const dateMode = dateRange.preset === "live" ? "authorLocalToday" : "";

  return `${DASHBOARD_SUMMARY_CACHE_PREFIX}${view}.${dateRange.startDate}.${dateRange.endDate}.${dateMode}`;
}

export function loadCachedDashboardSummary(page: Page, dateRange: DateRange) {
  if (!pageUsesDashboardSummary(page)) {
    return null;
  }

  const cacheKey = dashboardSummaryCacheKey(page, dateRange);

  try {
    const localCached = page === "activity" ? localStorage.getItem(cacheKey) : null;

    if (localCached) {
      const summary = sanitizeCachedDashboardSummary(JSON.parse(localCached) as Summary);

      if (summary) {
        return summary;
      }
    }
  } catch {
    //
  }

  try {
    const cached = sessionStorage.getItem(cacheKey);

    if (!cached) {
      return null;
    }

    return sanitizeCachedDashboardSummary(JSON.parse(cached) as Summary);
  } catch {
    return null;
  }
}

function sanitizeCachedDashboardSummary(summary: Summary): Summary | null {
  if (!summary || !sanitizeCachedActivitySummaryValue(summary.activitySummary)) {
    return null;
  }

  return summary;
}

function sanitizeCachedActivitySummaryValue(summary: ActivitySummary): ActivitySummary | null {
  if (!summary || !Array.isArray(summary.authors)) {
    return null;
  }

  return summary;
}

function canPersistDashboardSummary(page: Page, dateRange: DateRange, summary: Summary) {
  if (page !== "activity") {
    return true;
  }

  const nextActivitySummary = sanitizeCachedActivitySummaryValue(summary.activitySummary);

  if (!nextActivitySummary) {
    return false;
  }

  if (nextActivitySummary.snapshot?.status === "preparing") {
    return false;
  }

  const previous = loadCachedDashboardSummary(page, dateRange);
  const previousAuthors = previous?.activitySummary.authors ?? [];

  return nextActivitySummary.authors.length > 0 || previousAuthors.length === 0;
}

export function saveCachedDashboardSummary(page: Page, dateRange: DateRange, summary: Summary) {
  if (!pageUsesDashboardSummary(page)) {
    return;
  }

  if (!canPersistDashboardSummary(page, dateRange, summary)) {
    return;
  }

  const cacheKey = dashboardSummaryCacheKey(page, dateRange);

  try {
    sessionStorage.setItem(cacheKey, JSON.stringify(summary));
  } catch {
    // Browsers can reject storage writes in private mode or when the quota is full.
  }

  if (page !== "activity") {
    return;
  }

  try {
    localStorage.setItem(cacheKey, JSON.stringify(summary));
  } catch {
    // Browsers can reject storage writes in private mode or when the quota is full.
  }
}

export function clearDashboardSessionCaches() {
  const sessionPrefixes = [
    DASHBOARD_SUMMARY_CACHE_PREFIX,
    "AL.Dashboard.ActivityHourly.",
    "AL.Dashboard.ActivityReports.",
    "AL.Dashboard.ActivityReports.v2.",
    "AL.Dashboard.AnalyticsSummary",
    "AL.Dashboard.CalendarSummary"
  ];
  const localPrefixes = [
    DASHBOARD_SUMMARY_CACHE_PREFIX,
    LAST_AUTHORS_CACHE_PREFIX,
    ACTIVITY_SUMMARY_CACHE_PREFIX,
    "AL.Dashboard.ActivityHourly.",
    "AL.Dashboard.ActivityReports.",
    "AL.Dashboard.ActivityReports.v2.",
    SETTINGS_SUMMARY_CACHE_KEY
  ];

  for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
    const key = sessionStorage.key(index);

    if (key && sessionPrefixes.some((prefix) => key.startsWith(prefix))) {
      sessionStorage.removeItem(key);
    }
  }

  for (let index = localStorage.length - 1; index >= 0; index -= 1) {
    const key = localStorage.key(index);

    if (key && localPrefixes.some((prefix) => key.startsWith(prefix))) {
      localStorage.removeItem(key);
    }
  }
}

export function loadSavedDateRange(): DateRange {
  const savedRange = localStorage.getItem(DATE_RANGE_STORAGE_KEY);

  if (!savedRange) {
    return todayRange();
  }

  try {
    const parsed = JSON.parse(savedRange) as Partial<DateRange>;

    if (parsed.preset === "live") {
      return todayRange();
    }

    if (
      (parsed.preset === "yesterday" || parsed.preset === "custom") &&
      isDateInputValue(parsed.startDate) &&
      isDateInputValue(parsed.endDate)
    ) {
      return {
        startDate: parsed.startDate,
        endDate: parsed.startDate,
        preset: parsed.preset
      };
    }
  } catch {
    return todayRange();
  }

  return todayRange();
}

export function saveDateRange(dateRange: DateRange) {
  localStorage.setItem(DATE_RANGE_STORAGE_KEY, JSON.stringify(dateRange));
}

export function pageScrollStorageKey(page: Page) {
  return `${PAGE_SCROLL_STORAGE_PREFIX}${page}`;
}

export function getSavedPageScroll(page: Page) {
  return Number(sessionStorage.getItem(pageScrollStorageKey(page)) ?? 0);
}

export function savePageScroll(page: Page, scrollY: number) {
  sessionStorage.setItem(pageScrollStorageKey(page), String(scrollY));
}

function todayRange(): DateRange {
  const today = toDateInputValue(new Date());
  return { startDate: today, endDate: today, preset: "live" };
}

function toDateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function isDateInputValue(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
}
