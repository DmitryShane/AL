import type React from "react";
import { useEffect, useMemo } from "react";
import type { Report } from "../../types/dashboard";
import { formatSource } from "../../utils/format";
import {
  formatAuthorTime,
  formatReportActive,
  formatReportIdle,
  formatReportOvertime,
  formatReportType,
  formatTimeZoneLabel,
  reportTypeBadgeClassName
} from "../../pages/pageHelpers";
import { SourceIcon } from "../icons/SourceIcon";

type ReportsTableProps = {
  reports: Report[];
  total: number;
  page: number;
  pageSize: number;
  sourceFilter: string;
  sourceOptions: string[];
  hourFilter: string;
  loading: boolean;
  error: string | null;
  setPage: (value: number | ((current: number) => number)) => void;
  setPageSize: (value: number) => void;
  setSourceFilter: (value: string) => void;
  setHourFilter: (value: string) => void;
};

export function ReportsTable({
  reports,
  total,
  page,
  pageSize,
  sourceFilter,
  sourceOptions,
  hourFilter,
  loading,
  error,
  setPage,
  setPageSize,
  setSourceFilter,
  setHourFilter
}: ReportsTableProps) {
  const pageSizeOptions = [10, 25, 50];
  const hourOptions = Array.from({ length: 24 }, (_, hour) => String(hour));
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * pageSize;
  const pageEnd = Math.min(pageStart + reports.length, total);
  const timeZoneLabelByAuthor = useMemo(() => preferredTimeZoneLabelsByAuthor(reports), [reports]);

  useEffect(() => {
    if (!sourceFilter) {
      return;
    }

    const available = new Set(sourceOptions);

    if (!available.has(sourceFilter)) {
      setSourceFilter("");
    }
  }, [sourceOptions, sourceFilter, setSourceFilter]);

  return (
    <section className="panel table-panel">
      <div className="table-panel-header">
        <h2>Plugin Reports</h2>
        <div className="table-panel-filters">
          <label className="table-panel-filter table-panel-time-filter">
            <span>Time</span>
            <select value={hourFilter} onChange={(event) => setHourFilter(event.target.value)} aria-label="Reports hour">
              <option value="">All hours</option>
              {hourOptions.map((hour) => (
                <option key={hour} value={hour}>{formatHourOption(hour)}</option>
              ))}
            </select>
          </label>
          <label className="table-panel-filter">
            <span>Source</span>
            <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
              <option value="">All sources</option>
              {sourceOptions.map((key) => (
                <option key={key || "__none__"} value={key}>
                  {key ? formatSource(key) : "Unknown"}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <div className="table" style={{ "--reports-page-size": pageSize } as React.CSSProperties}>
        <div className="table-head">
          <span>Source</span>
          <span>Author</span>
          <span>Date</span>
          <span>Active</span>
          <span>Idle</span>
          <span>Overtime</span>
          <span>Recorded</span>
          <span>Type</span>
          <span>Timezone</span>
        </div>
        <div className="table-body">
          {loading ? <div className="table-state">Loading reports...</div> : null}
          {error ? <div className="table-state">{error}</div> : null}
          {!loading && !error && reports.length === 0 ? <div className="table-state">No reports for this period.</div> : null}
          {!loading && !error ? reports.map((report, index) => (
            <div className="table-row" key={`${report.recordedAt ?? "report"}-${index}`}>
              <span className="source-cell"><SourceIcon source={report.source} />{formatSource(report.source)}</span>
              <span>{report.displayName ?? report.author ?? "Unknown User"}</span>
              <span>{report.date ?? "-"}</span>
              <span>{formatReportActive(report)}</span>
              <span>{formatReportIdle(report)}</span>
              <span>{formatReportOvertime(report.overtimeActiveDeltaSeconds ?? 0)}</span>
              <span>{formatAuthorTime(report)}</span>
              <span className={reportTypeBadgeClassName(report.reportType)}>{formatReportType(report)}</span>
              <span>{timeZoneLabelByAuthor.get(reportAuthorKey(report)) ?? formatTimeZoneLabel(report) ?? "-"}</span>
            </div>
          )) : null}
        </div>
      </div>
      <div className="table-pagination">
        {total > 0 ? <span>Rows {pageStart + 1}-{pageEnd} of {total}</span> : null}
        <label>
          Rows per page
          <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
            {pageSizeOptions.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </label>
        <div className="pagination-buttons">
          <button className="primary-outline-button" onClick={() => setPage(1)} disabled={currentPage === 1}>First</button>
          <button className="primary-outline-button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={currentPage === 1}>Prev</button>
          <span className="pagination-counter">{currentPage} / {totalPages}</span>
          <button className="primary-outline-button" onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={currentPage === totalPages}>Next</button>
          <button className="primary-outline-button" onClick={() => setPage(totalPages)} disabled={currentPage === totalPages}>Last</button>
        </div>
      </div>
    </section>
  );
}

function formatHourOption(value: string) {
  return `${value.padStart(2, "0")}:00`;
}

function preferredTimeZoneLabelsByAuthor(reports: Report[]) {
  const labelsByAuthor = new Map<string, string>();
  const scoresByAuthor = new Map<string, number>();

  for (const report of reports) {
    const authorKey = reportAuthorKey(report);
    const candidates = timeZoneLabelCandidates(report);

    for (const candidate of candidates) {
      const score = timeZoneLabelScore(candidate);
      const currentScore = scoresByAuthor.get(authorKey) ?? -1;

      if (score > currentScore) {
        labelsByAuthor.set(authorKey, candidate);
        scoresByAuthor.set(authorKey, score);
      }
    }
  }

  return labelsByAuthor;
}

function reportAuthorKey(report: Report) {
  return report.author ?? report.displayName ?? "Unknown User";
}

function timeZoneLabelCandidates(report: Report) {
  const candidates: string[] = [];
  const ianaCity = report.timeZoneId?.split("/").pop()?.replace(/_/g, " ").trim();

  if (ianaCity) {
    candidates.push(ianaCity);
  }

  if (report.timeZoneDisplayName?.trim()) {
    candidates.push(report.timeZoneDisplayName.trim());
  }

  const formatted = formatTimeZoneLabel(report)?.trim();

  if (formatted) {
    candidates.push(formatted);
  }

  return candidates;
}

function timeZoneLabelScore(label: string) {
  if (isSpecificTimeZoneLabel(label)) {
    return 2;
  }

  return 1;
}

function isSpecificTimeZoneLabel(label: string) {
  const normalized = label.trim().toLowerCase();

  if (!normalized) {
    return false;
  }

  if (/^(utc|gmt)([+-]\d{1,2}(:?\d{2})?)?$/.test(normalized)) {
    return false;
  }

  return !new Set([
    "pacific",
    "eastern",
    "central",
    "mountain",
    "atlantic",
    "alaska",
    "hawaii",
    "pst",
    "pdt",
    "est",
    "edt",
    "cst",
    "cdt",
    "mst",
    "mdt",
    "local",
    "unknown"
  ]).has(normalized);
}
