export type AuthorsTableRow = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  source?: string;
  lastRecordedAt?: string;
  lastReceivedAt?: string;
  daySeconds: number;
  telegramDaySeconds: number;
  pluginDaySeconds: number;
  activeSeconds: number;
  idleSeconds: number;
  overtimeActiveSeconds: number;
  breakSeconds: number;
  productivity: number;
  status?: "online" | "stale";
  alertStats?: {
    total: number;
    critical: number;
    warning: number;
  };
};

type AuthorsTableProps = {
  authors: AuthorsTableRow[];
  emptyMessage: string;
};

export function AuthorsTable({ authors, emptyMessage }: AuthorsTableProps) {
  return (
    <div className="authors-table">
      <div className="authors-table-head">
        <span>Author</span>
        <span>Team</span>
        <span>Day Time (Telegram)</span>
        <span>Day Time (Plugin)</span>
        <span>Active Time</span>
        <span>Idle Time</span>
        <span>Overtime</span>
        <span>Break Time</span>
        <span>Productivity</span>
        <span>Status</span>
        <span>Plugin</span>
        <span>Last Report</span>
      </div>
      {authors.map((author) => (
        <div className="authors-row" key={author.rawAuthor}>
          <div className="author-cell" title={author.authorEmail || author.rawAuthor}>
            <span className="avatar">{initials(author.displayName)}</span>
            <div>
              <strong>{author.displayName}</strong>
              <small className="author-email" title={author.authorEmail || author.rawAuthor}>{author.authorEmail || author.rawAuthor}</small>
            </div>
          </div>
          <span>{author.team || "-"}</span>
          <strong>{formatDuration(author.telegramDaySeconds ?? author.daySeconds)}</strong>
          <strong>{formatDuration(author.pluginDaySeconds ?? author.activeSeconds + author.idleSeconds)}</strong>
          <strong>{formatDuration(author.activeSeconds)}</strong>
          <span>{formatDuration(author.idleSeconds)}</span>
          <strong>{formatDuration(author.overtimeActiveSeconds)}</strong>
          <span className={breakClassName(author.breakSeconds)}>{formatMinutes(author.breakSeconds)}</span>
          <strong className={productivityClassName(author.productivity)}>{author.productivity.toFixed(2)}%</strong>
          <span className="author-status-stack">
            <span className={statusBadgeClassName(author.status)}>{formatStatus(author)}</span>
          </span>
          <span>{formatSource(author.source)}</span>
          <span>{formatTimestamp(author.lastRecordedAt)}</span>
        </div>
      ))}
      {!authors.length ? <p className="empty-table">{emptyMessage}</p> : null}
    </div>
  );
}

function initials(value: string) {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function formatDuration(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function formatMinutes(seconds: number) {
  return `${Math.round(seconds / 60)}m`;
}

function formatSource(source?: string) {
  if (source === "ual") {
    return "Unity";
  }

  return source ?? "unknown";
}

function formatTimestamp(value?: string) {
  if (!value) {
    return "-";
  }

  return new Date(value).toLocaleString();
}

function formatStatus(author: AuthorsTableRow) {
  if (author.status === "stale") {
    return author.lastReceivedAt ? "Offline" : "No reports";
  }

  return "Online";
}

function statusBadgeClassName(status?: "online" | "stale") {
  return status === "stale" ? "status-badge stale" : "status-badge online";
}

function productivityClassName(value: number) {
  if (value > 80) {
    return "metric-value good";
  }

  if (value > 60) {
    return "metric-value warning";
  }

  return "metric-value bad";
}

function breakClassName(seconds: number) {
  return seconds > 3600 ? "metric-value bad" : "metric-value";
}
