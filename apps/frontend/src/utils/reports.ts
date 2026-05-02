import type { Report } from "../types/dashboard";
import { formatOffsetTimestampTime } from "./date";

export function formatReportMinutes(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainingSeconds = rounded % 60;

  if (hours > 0) {
    if (remainingSeconds > 0) {
      return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(remainingSeconds).padStart(2, "0")}s`;
    }

    return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  }

  if (minutes > 0) {
    if (remainingSeconds > 0) {
      return `${minutes}m ${String(remainingSeconds).padStart(2, "0")}s`;
    }

    return `${minutes}m`;
  }

  return `${remainingSeconds}s`;
}

export function formatReportOvertime(seconds: number) {
  return seconds > 0 ? formatReportMinutes(seconds) : "-";
}

export function formatReportActive(report: Report) {
  return isNonActivityReport(report) ? "-" : formatReportMinutes(report.activeDeltaSeconds ?? 0);
}

export function formatReportIdle(report: Report) {
  return isNonActivityReport(report) ? "-" : formatReportMinutes(report.idleDeltaSeconds ?? 0);
}

export function isNonActivityReport(report: Report) {
  return report.source === "telegram" || report.reportType === "telegram" || report.source === "discord" || report.reportType === "meeting";
}

export function formatAuthorTime(report: Report) {
  if (!report.recordedAt) {
    return "-";
  }

  const recordedAt = new Date(report.recordedAt);

  if (!report.timeZoneId) {
    return formatOffsetTimestampTime(report.recordedAt);
  }

  try {
    return new Intl.DateTimeFormat(undefined, { timeStyle: "short", timeZone: report.timeZoneId }).format(recordedAt);
  } catch {
    return formatOffsetTimestampTime(report.recordedAt);
  }
}

export function formatTimeZoneLabel(report: Report) {
  if (report.timeZoneDisplayName) {
    return report.timeZoneDisplayName;
  }

  return report.timeZoneId;
}
