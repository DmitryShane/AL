export const REFRESH_INTERVAL_MS = 10000;
export const PAGE_STORAGE_KEY = "AL.Dashboard.Page";
export const PAGE_SCROLL_STORAGE_PREFIX = "AL.Dashboard.PageScroll.";
export const ACTIVITY_AUTHOR_STORAGE_KEY = "AL.Dashboard.ActivityAuthor";
export const DATE_RANGE_STORAGE_KEY = "AL.Dashboard.DateRange";
export const REPORTS_PAGE_STORAGE_KEY = "AL.Dashboard.PluginReportsPage";
export const SETTINGS_TAB_STORAGE_KEY = "AL.Dashboard.SettingsTab";
export const AUTH_HINT_STORAGE_KEY = "AL.Dashboard.Authenticated";
export const DASHBOARD_SUMMARY_CACHE_PREFIX = "AL.Dashboard.Summary.";
export const ANALYTICS_SUMMARY_CACHE_KEY = "AL.Dashboard.AnalyticsSummary";
export const CALENDAR_SUMMARY_CACHE_KEY = "AL.Dashboard.CalendarSummary";

export const MEETING_SUMMARY_LANGUAGES = [
  "English",
  "Spanish",
  "French",
  "German",
  "Portuguese",
  "Italian",
  "Russian",
  "Chinese",
  "Japanese",
  "Korean"
];

export const MEETING_AUDIO_RETENTION_OPTIONS = [
  { label: "Do not keep recordings", value: 0 },
  { label: "1 hour", value: 3600 },
  { label: "2 hours", value: 7200 },
  { label: "6 hours", value: 21600 },
  { label: "1 day", value: 86400 },
  { label: "3 days", value: 259200 },
  { label: "7 days", value: 604800 }
];
