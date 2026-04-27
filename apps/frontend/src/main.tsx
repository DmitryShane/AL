import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, Clock, RefreshCw, Server, UserRound } from "lucide-react";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

type Health = {
  ok: boolean;
  mongo: boolean;
};

type Report = {
  source?: string;
  author?: string;
  projectId?: string;
  date?: string;
  firstActivity?: string;
  lastActivity?: string;
  activeSeconds?: number;
  idleSeconds?: number;
  overtimeActiveSeconds?: number;
  recordedAt?: string;
  receivedAt?: string;
};

type Summary = {
  authors: string[];
  reports: Report[];
  intervalSettings: {
    defaultSendIntervalSeconds: number;
    authors: Array<{ author: string; sendIntervalSeconds: number }>;
  };
};

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);

    try {
      const [healthResponse, summaryResponse] = await Promise.all([
        fetch(`${API_URL}/api/v1/health`),
        fetch(`${API_URL}/api/v1/reports/summary`)
      ]);

      if (!healthResponse.ok || !summaryResponse.ok) {
        throw new Error("Backend request failed");
      }

      setHealth(await healthResponse.json());
      setSummary(await summaryResponse.json());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const totals = useMemo(() => {
    const reports = summary?.reports ?? [];

    return reports.reduce(
      (acc, report) => {
        acc.active += report.activeSeconds ?? 0;
        acc.idle += report.idleSeconds ?? 0;
        acc.overtime += report.overtimeActiveSeconds ?? 0;
        return acc;
      },
      { active: 0, idle: 0, overtime: 0 }
    );
  }, [summary]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Activity Logger</p>
          <h1>AL Dashboard</h1>
        </div>
        <button className="icon-button" onClick={() => void load()} aria-label="Refresh dashboard">
          <RefreshCw size={18} />
        </button>
      </header>

      <section className="status-strip">
        <Metric icon={<Server size={18} />} label="Backend" value={health?.ok ? "Online" : "Offline"} muted={!health?.ok} />
        <Metric icon={<Activity size={18} />} label="Reports" value={String(summary?.reports.length ?? 0)} />
        <Metric icon={<UserRound size={18} />} label="Authors" value={String(summary?.authors.length ?? 0)} />
        <Metric
          icon={<Clock size={18} />}
          label="Global interval"
          value={`${summary?.intervalSettings.defaultSendIntervalSeconds ?? 0}s`}
        />
      </section>

      {loading ? <p className="notice">Loading dashboard data...</p> : null}
      {error ? <p className="notice error">{error}</p> : null}

      <section className="content-grid">
        <div className="panel">
          <h2>Activity Totals</h2>
          <div className="totals">
            <Duration label="Active" seconds={totals.active} />
            <Duration label="Idle" seconds={totals.idle} />
            <Duration label="Overtime" seconds={totals.overtime} />
          </div>
        </div>

        <div className="panel">
          <h2>Author Intervals</h2>
          <div className="list">
            {summary?.intervalSettings.authors.length ? (
              summary.intervalSettings.authors.map((item) => (
                <div className="row" key={item.author}>
                  <span>{item.author}</span>
                  <strong>{item.sendIntervalSeconds}s</strong>
                </div>
              ))
            ) : (
              <p className="empty">No author overrides yet.</p>
            )}
          </div>
        </div>
      </section>

      <section className="panel table-panel">
        <h2>Latest Reports</h2>
        <div className="table">
          <div className="table-head">
            <span>Source</span>
            <span>Author</span>
            <span>Date</span>
            <span>Active</span>
            <span>Idle</span>
            <span>Received</span>
          </div>
          {(summary?.reports ?? []).map((report, index) => (
            <div className="table-row" key={`${report.recordedAt ?? "report"}-${index}`}>
              <span>{report.source ?? "unknown"}</span>
              <span>{report.author ?? "Unknown User"}</span>
              <span>{report.date ?? "-"}</span>
              <span>{formatDuration(report.activeSeconds ?? 0)}</span>
              <span>{formatDuration(report.idleSeconds ?? 0)}</span>
              <span>{formatTimestamp(report.receivedAt)}</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}

function Metric({ icon, label, value, muted = false }: { icon: React.ReactNode; label: string; value: string; muted?: boolean }) {
  return (
    <div className={muted ? "metric muted" : "metric"}>
      {icon}
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function Duration({ label, seconds }: { label: string; seconds: number }) {
  return (
    <div className="duration">
      <span>{label}</span>
      <strong>{formatDuration(seconds)}</strong>
    </div>
  );
}

function formatDuration(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function formatTimestamp(value?: string) {
  if (!value) {
    return "-";
  }

  return new Date(value).toLocaleString();
}

createRoot(document.getElementById("root")!).render(<App />);
