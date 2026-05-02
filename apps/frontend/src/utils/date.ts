import type { DateRange } from "../types/dashboard";

export function toDateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function todayRange(): DateRange {
  const today = toDateInputValue(new Date());
  return { startDate: today, endDate: today, preset: "live" };
}

export function yesterdayRange(): DateRange {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const date = toDateInputValue(yesterday);
  return { startDate: date, endDate: date, preset: "yesterday" };
}

export function formatOffsetTimestampTime(value: string) {
  const match = value.match(/T(\d{2}):(\d{2})/);

  if (match) {
    return `${match[1]}:${match[2]}`;
  }

  return new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date(value));
}
