import { toDateInputValue } from "./date";

export function monthIndexes() {
  return Array.from({ length: 12 }, (_, index) => index);
}

export function toCalendarDate(year: number, month: number, day: number) {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function uniqueDates(dates: string[]) {
  return Array.from(new Set(dates)).sort();
}

export function dateRangeList(startDate: string, endDate: string) {
  const start = new Date(startDate);
  const end = new Date(endDate);
  const cursor = new Date(start <= end ? start : end);
  const last = new Date(start <= end ? end : start);
  const dates = [];

  while (cursor <= last) {
    dates.push(toDateInputValue(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }

  return dates;
}
