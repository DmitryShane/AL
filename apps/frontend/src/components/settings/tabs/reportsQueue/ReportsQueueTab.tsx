import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../../../../api/client";
import { SourceIcon } from "../../../icons/SourceIcon";
import type { ReportsQueueReport, ReportsQueueStatus, ServerStatsServiceStatus } from "../../../../types/dashboard";
import { formatSource } from "../../../../utils/format";
import { localBrowserStorage, readStorageItem, writeStorageCache } from "../../../../utils/browserStorage";
import { formatDateTime } from "../../serverStatsFormatters";

const REPORTS_QUEUE_REFRESH_MS = 5000;
const REPORTS_QUEUE_CACHE_KEY = "AL.Dashboard.ReportsQueue.Status";

export function ReportsQueueTab() {
  const [status, setStatus] = useState<ReportsQueueStatus | null>(() => readCachedReportsQueueStatus());
  const [loading, setLoading] = useState(status === null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const loadStatus = useCallback(async () => {
    setRefreshing(true);

    try {
      const response = await apiFetch("/api/v1/settings/reports-queue");

      if (!response.ok) {
        throw new Error("Reports queue request failed");
      }

      const payload = (await response.json()) as ReportsQueueStatus;
      setStatus(payload);
      saveCachedReportsQueueStatus(payload);
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Reports queue request failed");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    const intervalId = window.setInterval(() => void loadStatus(), REPORTS_QUEUE_REFRESH_MS);

    return () => window.clearInterval(intervalId);
  }, [loadStatus]);

  return (
    <section className="panel reports-queue-panel" data-doc-target="settings-reportsQueue">
      <div className="reports-queue-header">
        <div>
          <h2>Reports Queue</h2>
          <p className="settings-caption">Async report ingest worker and queue health.</p>
        </div>
        <div className="reports-queue-actions">
          {status?.generatedAt ? <span>Updated {formatDateTime(status.generatedAt)}</span> : null}
          <button className="server-stats-refresh-button" onClick={() => void loadStatus()} disabled={refreshing}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {loading ? <p className="notice">Loading reports queue...</p> : null}
      {error ? <p className="notice">Could not load reports queue: {error}</p> : null}

      {status ? (
        <>
          <div className="reports-queue-overview">
            <WorkerStatusCard status={status} />
            <QueueMetric label="Queued" value={status.counts.queued} tone={status.counts.queued > 0 ? "warning" : "ok"} />
            <QueueMetric label="Processing" value={status.counts.processing} tone="neutral" />
            <QueueMetric label="Processed last hour" value={status.counts.processedLastHour} tone="ok" />
            <QueueMetric label="Failed" value={status.counts.failed} tone={status.counts.failed > 0 ? "danger" : "ok"} />
            <QueueMetric label="Oldest queued" value={formatOldestQueuedAge(status.oldestQueued?.ageSeconds)} tone={status.oldestQueued ? "warning" : "ok"} />
          </div>

          <ReportsQueueTable title="Recent reports" reports={status.recentReports} emptyLabel="No reports have been queued yet." />

          {status.failedReports.length > 0 ? (
            <ReportsQueueTable title="Failed reports" reports={status.failedReports} emptyLabel="No failed reports." isFailedTable />
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function WorkerStatusCard({ status }: { status: ReportsQueueStatus }) {
  return (
    <div className={`reports-queue-worker reports-queue-worker-${status.worker.status}`}>
      <div>
        <span>Worker</span>
        <strong>{formatWorkerStatus(status.worker.status)}</strong>
      </div>
      <small>{status.worker.unit}</small>
      <small>{formatWorkerDetail(status.worker)}</small>
      <small>Last processed: {status.worker.lastProcessedAt ? formatDateTime(status.worker.lastProcessedAt) : "none"}</small>
    </div>
  );
}

function QueueMetric({
  label,
  value,
  tone
}: {
  label: string;
  value: number | string;
  tone: "ok" | "warning" | "danger" | "neutral";
}) {
  return (
    <div className={`reports-queue-metric reports-queue-metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReportsQueueTable({
  title,
  reports,
  emptyLabel,
  isFailedTable = false
}: {
  title: string;
  reports: ReportsQueueReport[];
  emptyLabel: string;
  isFailedTable?: boolean;
}) {
  const pageSizeOptions = [10, 25, 50];
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(10);
  const total = reports.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageStart = total > 0 ? (currentPage - 1) * pageSize : 0;
  const pageEnd = Math.min(pageStart + pageSize, total);
  const visibleReports = reports.slice(pageStart, pageEnd);

  useEffect(() => {
    setPage((value) => Math.min(value, totalPages));
  }, [totalPages]);

  function setPageSize(value: number) {
    setPageSizeState(value);
    setPage(1);
  }

  return (
    <section className="reports-queue-table-panel">
      <div className="reports-queue-section-header">
        <h3>{title}</h3>
        <span>{total} available</span>
      </div>
      <div className="reports-queue-table-shell">
        <div className="reports-queue-table">
          <div className="reports-queue-table-head">
            <span>Source</span>
            <span>Author</span>
            <span>Received</span>
            <span>Status</span>
            <span>Chunks</span>
            <span>Attempts</span>
            <span>Assembly</span>
            <span>Processing</span>
            <span>Error</span>
          </div>
          {visibleReports.length > 0 ? (
            visibleReports.map((report) => (
              <ReportQueueRow key={report.id} report={report} isFailedTable={isFailedTable} />
            ))
          ) : (
            <p className="reports-queue-empty">{emptyLabel}</p>
          )}
        </div>
      </div>
      <div className="table-pagination reports-queue-pagination">
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

function ReportQueueRow({ report, isFailedTable }: { report: ReportsQueueReport; isFailedTable: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const hasChunks = Array.isArray(report.chunks) && report.chunks.length > 0;

  return (
    <>
      <div className={`reports-queue-row reports-queue-row-${statusClassName(report.stage || report.status)}`}>
        <span className="source-cell" title={report.source || undefined}>
          <SourceIcon source={report.source} />
          {formatSource(report.source)}
        </span>
        <span title={formatAuthorLaneTitle(report)}>{report.displayName || report.author || "unknown"}</span>
        <span title={report.receivedAt || undefined}>{formatDateTime(report.receivedAt)}</span>
        <span title={formatStageTitle(report)}>
          <StatusBadge status={report.stage || report.status} label={report.stageLabel || formatStatusLabel(report.status)} />
        </span>
        <span>
          {hasChunks ? (
            <button className="reports-queue-chunks-button" onClick={() => setExpanded((value) => !value)}>
              {formatChunkProgress(report)}
            </button>
          ) : (
            "-"
          )}
        </span>
        <span>{report.attempts}</span>
        <span>{formatAssemblyState(report)}</span>
        <span>{formatProcessingState(report)}</span>
        <span title={report.lastError || undefined}>{report.lastError || (isFailedTable ? "No error recorded" : "")}</span>
      </div>
      {hasChunks && expanded ? (
        <div className="reports-queue-chunk-details">
          {(report.chunks || []).map((chunk) => (
            <div className="reports-queue-chunk-row" key={`${report.id}-${chunk.chunkIndex}`}>
              <span>Chunk {chunk.chunkIndex}/{report.chunkCount || report.chunks?.length || "?"}</span>
              <span>{chunk.eventCount} events</span>
              <span>{formatDateTime(chunk.receivedAt)}</span>
              <StatusBadge status={chunk.status} label={formatStatusLabel(chunk.status)} />
              <span>{chunk.attempts ?? 0} attempts</span>
              <span>{formatDuration(chunk.processingSeconds)}</span>
              <span>{chunk.rawReportId}</span>
              <span title={chunk.lastError || undefined}>{chunk.lastError}</span>
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}

function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <span className={`reports-queue-status reports-queue-status-${statusClassName(status)}`}>{label || formatStatusLabel(status)}</span>;
}

function formatWorkerStatus(status: ServerStatsServiceStatus): string {
  if (status === "running") {
    return "Running";
  }

  if (status === "stopped") {
    return "Stopped";
  }

  return "Unknown";
}

function formatWorkerDetail(worker: ReportsQueueStatus["worker"]): string {
  const state = worker.subState ? `${worker.activeState} / ${worker.subState}` : worker.activeState;

  if (worker.status === "running" && worker.activeEnteredAt) {
    return `${state}, since ${formatDateTime(worker.activeEnteredAt)}`;
  }

  return state || "unknown";
}

function formatOldestQueuedAge(ageSeconds: number | null | undefined): string {
  if (ageSeconds === null || ageSeconds === undefined) {
    return "none";
  }

  return formatDuration(ageSeconds);
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) {
    return "none";
  }

  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }

  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);

  if (minutes < 60) {
    return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
  }

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

function formatStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    assembled: "Assembled",
    assembling: "Assembling",
    assembling_report: "Assembling report",
    failed: "Failed",
    processed: "Processed",
    processing: "Processing",
    processing_events: "Processing events",
    queued: "Queued",
    queued_for_processing: "Queued for processing",
    received: "Received",
    receiving: "Receiving",
    receiving_chunks: "Receiving chunks",
    running: "Running",
  };
  return labels[status] || status || "unknown";
}

function formatAssemblyState(report: ReportsQueueReport): string {
  const status = report.assemblyStatus || (report.assemblySeconds !== null && report.assemblySeconds !== undefined ? "done" : "pending");
  if (status === "done") {
    return `done · ${formatDuration(report.assemblySeconds)}`;
  }
  if (status === "running") {
    return "running";
  }
  if (status === "failed") {
    return "failed";
  }
  return "pending";
}

function formatProcessingState(report: ReportsQueueReport): string {
  const total = report.eventIngestTotal ?? 0;
  const processed = report.eventIngestProcessed ?? 0;

  if (total > 0 && processed < total) {
    return `${processed}/${total} events`;
  }

  const status = report.processingStatus || (report.processingSeconds !== null && report.processingSeconds !== undefined ? "done" : "pending");
  if (status === "done") {
    return `done · ${formatDuration(report.processingSeconds)}`;
  }
  if (status === "queued") {
    return "queued";
  }
  if (status === "running") {
    return "running";
  }
  if (status === "failed") {
    return "failed";
  }
  return "pending";
}

function formatAuthorLaneTitle(report: ReportsQueueReport): string {
  const parts = [report.author || "unknown"];

  if (report.authorKey) {
    parts.push(`lane: ${report.authorKey}`);
  }

  if (report.leaseOwner) {
    parts.push(`worker: ${report.leaseOwner}`);
  }

  return parts.join(" | ");
}

function formatChunkProgress(report: ReportsQueueReport): string {
  const received = report.chunksReceived ?? report.chunks?.length ?? 0;
  const total = report.chunkCount ?? report.chunks?.length ?? 0;
  const processed = report.chunksProcessed ?? 0;
  return `${received}/${total} received, ${processed}/${total} processed`;
}

function formatStageTitle(report: ReportsQueueReport): string {
  if (report.kind === "chunked") {
    return [
      report.stageLabel || formatStatusLabel(report.stage || report.status),
      `Chunks: ${formatChunkProgress(report)}`,
      `Assembly: ${formatAssemblyState(report)}`,
      `Processing: ${formatProcessingState(report)}`,
    ].join(" · ");
  }
  return formatStatusLabel(report.status);
}

function statusClassName(status: string): string {
  return (status || "unknown").replace(/[^a-z0-9_-]/gi, "").toLowerCase() || "unknown";
}

function readCachedReportsQueueStatus(): ReportsQueueStatus | null {
  try {
    const raw = readStorageItem(localBrowserStorage(), REPORTS_QUEUE_CACHE_KEY);

    if (!raw) {
      return null;
    }

    const status = JSON.parse(raw) as Partial<ReportsQueueStatus>;

    if (!status || !Array.isArray(status.recentReports) || !Array.isArray(status.failedReports)) {
      return null;
    }

    return status as ReportsQueueStatus;
  } catch {
    return null;
  }
}

function saveCachedReportsQueueStatus(status: ReportsQueueStatus) {
  writeStorageCache(localBrowserStorage(), REPORTS_QUEUE_CACHE_KEY, JSON.stringify(status));
}
