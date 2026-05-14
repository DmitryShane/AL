import { useEffect, useMemo, useState } from "react";
import { ChevronRight, RefreshCw } from "lucide-react";
import { apiFetch } from "../../../../api/client";
import type { ActivitySnapshotStatus, ActivitySnapshotStatusRow } from "../../../../types/dashboard";
import { formatTimestamp } from "../../../../pages/pageHelpers";

const SNAPSHOT_STATUS_CACHE_KEY = "AL.Dashboard.ActivitySnapshots.Status.v1";
const SNAPSHOT_TABLE_STATE_KEY = "AL.Dashboard.ActivitySnapshots.TableState.v1";
const SNAPSHOT_PAGE_SIZE_OPTIONS = [5, 10, 25];

type SnapshotTableState = {
  page: number;
  pageSize: number;
  collapsedDates: string[];
};

export function ActivitySnapshotsTab() {
  const [status, setStatus] = useState<ActivitySnapshotStatus | null>(() => loadCachedStatus());
  const [tableState, setTableState] = useState<SnapshotTableState>(() => loadTableState());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadStatus() {
    setLoading(!status);
    setError("");

    try {
      const response = await apiFetch("/api/v1/settings/activity-snapshots?limitDays=45");

      if (!response.ok) {
        throw new Error("Snapshot status request failed");
      }

      const payload = (await response.json()) as ActivitySnapshotStatus;
      setStatus(payload);
      saveCachedStatus(payload);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not load snapshot status.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadStatus();
    const intervalId = window.setInterval(() => void loadStatus(), 10000);

    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    saveTableState(tableState);
  }, [tableState]);

  const rowsByDate = useMemo(() => {
    const groups = new Map<string, ActivitySnapshotStatusRow[]>();

    for (const row of status?.rows ?? []) {
      const rows = groups.get(row.date) ?? [];
      rows.push(row);
      groups.set(row.date, rows);
    }

    return [...groups.entries()];
  }, [status]);

  const totalPages = Math.max(1, Math.ceil(rowsByDate.length / tableState.pageSize));
  const currentPage = Math.min(tableState.page, totalPages);
  const pageStart = rowsByDate.length ? (currentPage - 1) * tableState.pageSize : 0;
  const pageEnd = Math.min(pageStart + tableState.pageSize, rowsByDate.length);
  const visibleDateGroups = rowsByDate.slice(pageStart, pageEnd);
  const collapsedDates = new Set(tableState.collapsedDates);

  useEffect(() => {
    if (tableState.page <= totalPages) {
      return;
    }

    setTableState((current) => ({ ...current, page: totalPages }));
  }, [tableState.page, totalPages]);

  function setPage(page: number | ((value: number) => number)) {
    setTableState((current) => {
      const nextPage = typeof page === "function" ? page(current.page) : page;
      return { ...current, page: Math.min(Math.max(1, nextPage), totalPages) };
    });
  }

  function setPageSize(pageSize: number) {
    setTableState((current) => ({ ...current, page: 1, pageSize }));
  }

  function toggleDate(date: string) {
    setTableState((current) => {
      const dates = new Set(current.collapsedDates);

      if (dates.has(date)) {
        dates.delete(date);
      } else {
        dates.add(date);
      }

      return { ...current, collapsedDates: [...dates].sort() };
    });
  }

  return (
    <div className="panel activity-snapshots-panel">
      <div className="settings-panel-header">
        <div>
          <h2>Activity Snapshots</h2>
          <p className="settings-caption">Historical Activity snapshot materialization progress by date and author.</p>
        </div>
        <button className="primary-outline-button" onClick={() => void loadStatus()} disabled={loading}>
          <RefreshCw size={16} />
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {status ? (
        <div className="snapshot-summary-grid">
          <SnapshotMetric label="Ready" value={status.totals.ready} />
          <SnapshotMetric label="Processing" value={status.totals.processing} />
          <SnapshotMetric label="Next" value={status.totals.next} />
          <SnapshotMetric label="Pending" value={status.totals.pending} />
          <SnapshotMetric label="Live" value={status.totals.live} />
        </div>
      ) : null}

      {error ? <p className="settings-error">{error}</p> : null}

      <div className="profile-table-shell activity-snapshots-table-shell">
        <div className="profile-table activity-snapshots-table">
          <div className="profile-table-head">
            <span>Date</span>
            <span>Author</span>
            <span>Author snapshot</span>
            <span>Day snapshot</span>
            <span>Built at</span>
          </div>

          <div className="activity-snapshots-table-body">
            {visibleDateGroups.map(([date, rows]) => {
              const collapsed = collapsedDates.has(date);

              return (
                <div className="activity-snapshot-date-group" key={date}>
                  <button className="activity-snapshot-date-row" type="button" onClick={() => toggleDate(date)} aria-expanded={!collapsed}>
                    <span>
                      <ChevronRight size={16} className={collapsed ? "" : "expanded"} />
                      <strong>{date}</strong>
                    </span>
                    <span>{dateProgressLabel(rows)}</span>
                  </button>

                  {!collapsed ? rows.map((row) => (
                    <div className="profile-row" key={`${row.date}:${row.rawAuthor}`}>
                      <span />
                      <span>
                        <strong>{row.displayName}</strong>
                        <small>{row.rawAuthor}{row.timeZoneId ? ` · ${row.timeZoneId}` : ""}</small>
                      </span>
                      <span>
                        <span className={`snapshot-status-pill snapshot-status-pill--${row.status}`}>{snapshotStatusLabel(row.status)}</span>
                      </span>
                      <span>{row.daySnapshotReady ? "Ready" : "Not ready"}</span>
                      <span>{row.builtAt ? formatTimestamp(row.builtAt) : "—"}</span>
                    </div>
                  )) : null}
                </div>
              );
            })}

            {!loading && !rowsByDate.length ? <p className="profile-table-empty">No historical snapshot candidates found.</p> : null}
            {loading && !rowsByDate.length ? <p className="profile-table-empty">Loading snapshot status...</p> : null}
          </div>
        </div>
      </div>

      <div className="table-pagination activity-snapshots-pagination">
        {rowsByDate.length > 0 ? <span>Dates {pageStart + 1}-{pageEnd} of {rowsByDate.length}</span> : null}
        <label>
          Dates per page
          <select value={tableState.pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
            {SNAPSHOT_PAGE_SIZE_OPTIONS.map((option) => (
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
    </div>
  );
}

function SnapshotMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="snapshot-summary-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function snapshotStatusLabel(status: ActivitySnapshotStatusRow["status"]) {
  if (status === "ready") {
    return "Ready";
  }

  if (status === "next") {
    return "Next";
  }

  if (status === "processing") {
    return "Processing";
  }

  if (status === "live") {
    return "Live";
  }

  return "Pending";
}

function dateProgressLabel(rows: ActivitySnapshotStatusRow[]) {
  const ready = rows.filter((row) => row.authorSnapshotReady).length;
  const live = rows.filter((row) => row.status === "live").length;
  return `${ready}/${rows.length} ready${live ? ` · ${live} live` : ""}`;
}

function loadCachedStatus() {
  try {
    const raw = sessionStorage.getItem(SNAPSHOT_STATUS_CACHE_KEY);
    return raw ? JSON.parse(raw) as ActivitySnapshotStatus : null;
  } catch {
    return null;
  }
}

function saveCachedStatus(status: ActivitySnapshotStatus) {
  try {
    sessionStorage.setItem(SNAPSHOT_STATUS_CACHE_KEY, JSON.stringify(status));
  } catch {
    // Ignore storage failures; live refresh still works.
  }
}

function loadTableState(): SnapshotTableState {
  try {
    const raw = sessionStorage.getItem(SNAPSHOT_TABLE_STATE_KEY);
    const parsed = raw ? JSON.parse(raw) as Partial<SnapshotTableState> : {};
    const page = Number(parsed.page ?? 1);
    const pageSize = Number(parsed.pageSize ?? 5);

    return {
      page: Number.isInteger(page) && page > 0 ? page : 1,
      pageSize: SNAPSHOT_PAGE_SIZE_OPTIONS.includes(pageSize) ? pageSize : 5,
      collapsedDates: Array.isArray(parsed.collapsedDates) ? parsed.collapsedDates.filter((item) => typeof item === "string") : [],
    };
  } catch {
    return { page: 1, pageSize: 5, collapsedDates: [] };
  }
}

function saveTableState(state: SnapshotTableState) {
  try {
    sessionStorage.setItem(SNAPSHOT_TABLE_STATE_KEY, JSON.stringify(state));
  } catch {
    // Ignore storage failures; table controls still work for the current mount.
  }
}
