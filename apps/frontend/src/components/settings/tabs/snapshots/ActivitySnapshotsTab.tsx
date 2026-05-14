import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { apiFetch } from "../../../../api/client";
import type { ActivitySnapshotStatus, ActivitySnapshotStatusRow } from "../../../../types/dashboard";
import { formatTimestamp } from "../../../../pages/pageHelpers";

export function ActivitySnapshotsTab() {
  const [status, setStatus] = useState<ActivitySnapshotStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadStatus() {
    setLoading(true);
    setError("");

    try {
      const response = await apiFetch("/api/v1/settings/activity-snapshots?limitDays=45");

      if (!response.ok) {
        throw new Error("Snapshot status request failed");
      }

      setStatus((await response.json()) as ActivitySnapshotStatus);
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

  const rowsByDate = useMemo(() => {
    const groups = new Map<string, ActivitySnapshotStatusRow[]>();

    for (const row of status?.rows ?? []) {
      const rows = groups.get(row.date) ?? [];
      rows.push(row);
      groups.set(row.date, rows);
    }

    return [...groups.entries()];
  }, [status]);

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
          <SnapshotMetric label="Next" value={status.totals.next} />
          <SnapshotMetric label="Pending" value={status.totals.pending} />
          <SnapshotMetric label="Live" value={status.totals.live} />
          <SnapshotMetric label="Version" value={status.snapshotVersion} />
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

          {rowsByDate.map(([date, rows]) =>
            rows.map((row, index) => (
              <div className="profile-row" key={`${row.date}:${row.rawAuthor}`}>
                <span>{index === 0 ? date : ""}</span>
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
            ))
          )}

          {!loading && !rowsByDate.length ? <p className="profile-table-empty">No historical snapshot candidates found.</p> : null}
          {loading && !rowsByDate.length ? <p className="profile-table-empty">Loading snapshot status...</p> : null}
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

  if (status === "live") {
    return "Live";
  }

  return "Pending";
}
