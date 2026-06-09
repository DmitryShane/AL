import {
  AUTH_HINT_STORAGE_KEY,
  DATE_RANGE_STORAGE_KEY,
  DASHBOARD_SUMMARY_CACHE_PREFIX,
  PAGE_SCROLL_STORAGE_PREFIX,
  SESSION_USER_PREVIEW_STORAGE_KEY
} from "../constants/dashboard";
import {
  clearDashboardCaches,
  localBrowserStorage,
  readStorageItem,
  removeStorageItem,
  sessionBrowserStorage,
  writeStorageCache,
  writeStorageState
} from "./browserStorage";
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
  if (typeof window === "undefined" || readStorageItem(localBrowserStorage(), AUTH_HINT_STORAGE_KEY) !== "true") {
    return null;
  }

  try {
    const raw = readStorageItem(sessionBrowserStorage(), SESSION_USER_PREVIEW_STORAGE_KEY);

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
    removeStorageItem(sessionBrowserStorage(), SESSION_USER_PREVIEW_STORAGE_KEY);
    return;
  }

  const avatarUrl = typeof user.avatarUrl === "string" && user.avatarUrl.trim() ? user.avatarUrl.trim() : undefined;
  writeStorageState(
    sessionBrowserStorage(),
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
}

function lastAuthorsCacheKey(dateRange: DateRange) {
  const dateMode = dateRange.preset === "live" ? "authorLocalToday" : "";
  return `${LAST_AUTHORS_CACHE_PREFIX}${dateRange.startDate}.${dateRange.endDate}.${dateMode}`;
}

export function readCachedAuthors(dateRange: DateRange): AuthorRow[] {
  try {
    const raw = readStorageItem(localBrowserStorage(), lastAuthorsCacheKey(dateRange));

    if (!raw) {
      return [];
    }

    const authors = JSON.parse(raw) as unknown;

    if (!Array.isArray(authors)) {
      return [];
    }

    return sanitizeCachedAuthorRows(authors);
  } catch {
    return [];
  }
}

export function saveCachedAuthors(dateRange: DateRange, authors: AuthorRow[]) {
  writeStorageCache(localBrowserStorage(), lastAuthorsCacheKey(dateRange), JSON.stringify(authors));
}

function activitySummaryCacheKey(dateRange: DateRange) {
  const dateMode = dateRange.preset === "live" ? "authorLocalToday" : "";
  return `${ACTIVITY_SUMMARY_CACHE_PREFIX}${dateRange.startDate}.${dateRange.endDate}.${dateMode}`;
}

export function readCachedActivitySummary(dateRange: DateRange): ActivitySummary | null {
  try {
    const raw = readStorageItem(localBrowserStorage(), activitySummaryCacheKey(dateRange));

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

    writeStorageCache(localBrowserStorage(), activitySummaryCacheKey(dateRange), JSON.stringify(summary));
  } catch {
    // Browsers can reject storage writes in private mode or when the quota is full.
  }
}

export function readCachedSettingsSummary(): Summary | null {
  try {
    const raw = readStorageItem(localBrowserStorage(), SETTINGS_SUMMARY_CACHE_KEY);

    if (!raw) {
      return null;
    }

    return sanitizeCachedDashboardSummary(JSON.parse(raw) as Summary);
  } catch {
    return null;
  }
}

export function saveCachedSettingsSummary(summary: Summary) {
  writeStorageCache(localBrowserStorage(), SETTINGS_SUMMARY_CACHE_KEY, JSON.stringify(summary));
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
    const localCached = page === "activity" ? readStorageItem(localBrowserStorage(), cacheKey) : null;

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
    const cached = readStorageItem(sessionBrowserStorage(), cacheKey);

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

  return {
    ...summary,
    authors: sanitizeCachedAuthorRows(summary.authors)
  };
}

function sanitizeCachedAuthorRows(authors: unknown[]): AuthorRow[] {
  return authors.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }

    const author = item as Partial<AuthorRow>;
    const rawAuthor = typeof author.rawAuthor === "string" ? author.rawAuthor : "";
    const displayName = typeof author.displayName === "string" ? author.displayName : rawAuthor;

    if (!rawAuthor || !displayName) {
      return [];
    }

    return [{
      ...author,
      rawAuthor,
      displayName,
      daySeconds: numberOrZero(author.daySeconds),
      telegramDaySeconds: numberOrZero(author.telegramDaySeconds),
      pluginDaySeconds: numberOrZero(author.pluginDaySeconds),
      rawPluginDaySeconds: numberOrUndefined(author.rawPluginDaySeconds),
      telegramToFirstActivitySeconds: numberOrUndefined(author.telegramToFirstActivitySeconds),
      activeSeconds: numberOrZero(author.activeSeconds),
      idleSeconds: numberOrZero(author.idleSeconds),
      meetingSeconds: numberOrZero(author.meetingSeconds),
      breakSeconds: numberOrZero(author.breakSeconds),
      overtimeActiveSeconds: numberOrZero(author.overtimeActiveSeconds),
      productivity: numberOrZero(author.productivity),
      activityMix: Array.isArray(author.activityMix) ? author.activityMix : [],
      savedPrefabs: Array.isArray(author.savedPrefabs) ? author.savedPrefabs : [],
      overtimeActivityMix: Array.isArray(author.overtimeActivityMix) ? author.overtimeActivityMix : [],
      overtimeSavedPrefabs: Array.isArray(author.overtimeSavedPrefabs) ? author.overtimeSavedPrefabs : [],
      activityMixBySource: Array.isArray(author.activityMixBySource) ? author.activityMixBySource : [],
      savedPrefabsBySource: Array.isArray(author.savedPrefabsBySource) ? author.savedPrefabsBySource : [],
      overtimeActivityMixBySource: Array.isArray(author.overtimeActivityMixBySource) ? author.overtimeActivityMixBySource : [],
      overtimeSavedPrefabsBySource: Array.isArray(author.overtimeSavedPrefabsBySource) ? author.overtimeSavedPrefabsBySource : []
    } as AuthorRow];
  });
}

function numberOrZero(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function numberOrUndefined(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
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

  const payload = JSON.stringify(summary);
  writeStorageCache(sessionBrowserStorage(), cacheKey, payload);

  if (page !== "activity") {
    return;
  }

  writeStorageCache(localBrowserStorage(), cacheKey, payload);
}

export function clearDashboardSessionCaches() {
  clearDashboardCaches();
}

export function loadSavedDateRange(): DateRange {
  const savedRange = readStorageItem(localBrowserStorage(), DATE_RANGE_STORAGE_KEY);

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
      if (parsed.startDate === toDateInputValue(new Date()) && parsed.endDate === parsed.startDate) {
        return todayRange();
      }

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
  writeStorageState(localBrowserStorage(), DATE_RANGE_STORAGE_KEY, JSON.stringify(dateRange));
}

export function pageScrollStorageKey(page: Page) {
  return `${PAGE_SCROLL_STORAGE_PREFIX}${page}`;
}

export function getSavedPageScroll(page: Page) {
  return Number(readStorageItem(sessionBrowserStorage(), pageScrollStorageKey(page)) ?? 0);
}

export function savePageScroll(page: Page, scrollY: number) {
  writeStorageState(sessionBrowserStorage(), pageScrollStorageKey(page), String(scrollY));
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
