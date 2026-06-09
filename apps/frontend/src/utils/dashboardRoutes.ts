import type { Page } from "../types/dashboard";

const PAGE_PATHS: Record<Page, string> = {
  authors: "/authors",
  activity: "/activity",
  analytics: "/analytics",
  calendar: "/calendar",
  alerts: "/alerts",
  settings: "/settings"
};

export function pageToPath(page: Page) {
  return PAGE_PATHS[page];
}

export function pathToPage(pathname: string): Page | null {
  const normalized = normalizeDashboardPath(pathname);

  for (const [page, path] of Object.entries(PAGE_PATHS)) {
    if (normalized === path) {
      return page as Page;
    }
  }

  return null;
}

export function isKnownDashboardPath(pathname: string) {
  return pathToPage(pathname) !== null;
}

export function normalizeDashboardPath(pathname: string) {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  return normalized;
}
