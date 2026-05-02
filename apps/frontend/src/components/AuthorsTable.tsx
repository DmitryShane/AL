export type AuthorsTableRow = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  source?: string;
  lastRecordedAt?: string;
  lastReceivedAt?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  daySeconds: number;
  telegramDaySeconds: number;
  pluginDaySeconds: number;
  activeSeconds: number;
  idleSeconds: number;
  meetingSeconds: number;
  overtimeActiveSeconds: number;
  breakSeconds: number;
  productivity: number;
  authorColor?: string;
  status?: "online" | "stale";
  stalePresence?: "telegram" | "reports" | "both";
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
        <span>Meeting</span>
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
            <span className="avatar" style={avatarStyle(author.authorColor)}>{initials(author.displayName)}</span>
            <div>
              <strong>{author.displayName}</strong>
              <small className="author-email" title={author.authorEmail || author.rawAuthor}>{author.authorEmail || author.rawAuthor}</small>
            </div>
          </div>
          <span>{author.team || "-"}</span>
          <span>{formatDuration(author.telegramDaySeconds ?? author.daySeconds)}</span>
          <span>{formatDuration(author.pluginDaySeconds ?? author.activeSeconds + author.idleSeconds)}</span>
          <span>{formatDuration(author.activeSeconds)}</span>
          <span>{formatDuration(author.idleSeconds)}</span>
          <span>{formatDuration(author.meetingSeconds ?? 0)}</span>
          <span>{formatDuration(author.overtimeActiveSeconds)}</span>
          <span className={breakClassName(author.breakSeconds)}>{formatMinutes(author.breakSeconds)}</span>
          <strong className={productivityClassName(author)}>{author.productivity.toFixed(2)}%</strong>
          <span className="author-status-stack">
            <span className={statusBadgeClassName(author.status, author.stalePresence)}>{formatStatus(author)}</span>
          </span>
          <span>{formatSource(author.source)}</span>
          <span className="author-last-report" title={formatTimestamp(author.lastRecordedAt)}>
            <span>{formatAuthorDate(author)}</span>
            <span>{formatAuthorTime(author)}</span>
          </span>
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

function avatarStyle(authorColor?: string) {
  return authorColor ? { backgroundColor: authorColor } : undefined;
}

function formatDuration(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function formatMinutes(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));

  if (rounded < 3600) {
    return `${Math.round(rounded / 60)}m`;
  }

  return formatDuration(rounded);
}

function formatSource(source?: string) {
  if (source === "ual") {
    return "Unity";
  }

  if (source === "bal") {
    return "Blender";
  }

  if (source === "fch") {
    return "FigmaWeb";
  }

  if (source === "fig") {
    return "FigmaApp";
  }

  if (source === "vsc") {
    return "VS Code";
  }

  if (source === "cur") {
    return "Cursor";
  }

  if (source === "telegram") {
    return "Telegram";
  }

  if (source === "discord") {
    return "Discord";
  }

  return source ?? "unknown";
}

function formatTimestamp(value?: string) {
  if (!value) {
    return "-";
  }

  return new Date(value).toLocaleString();
}

function formatAuthorDate(author: AuthorsTableRow) {
  if (!author.lastRecordedAt) {
    return "-";
  }

  const recordedAt = new Date(author.lastRecordedAt);

  if (!author.timeZoneId) {
    return formatOffsetTimestampDate(author.lastRecordedAt);
  }

  try {
    return new Intl.DateTimeFormat(undefined, {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      timeZone: author.timeZoneId
    }).format(recordedAt);
  } catch {
    return formatOffsetTimestampDate(author.lastRecordedAt);
  }
}

function formatAuthorTime(author: AuthorsTableRow) {
  if (!author.lastRecordedAt) {
    return "-";
  }

  const recordedAt = new Date(author.lastRecordedAt);

  if (!author.timeZoneId) {
    return formatOffsetTimestampTime(author.lastRecordedAt);
  }

  try {
    return new Intl.DateTimeFormat(undefined, {
      timeStyle: "short",
      timeZone: author.timeZoneId
    }).format(recordedAt);
  } catch {
    return formatOffsetTimestampTime(author.lastRecordedAt);
  }
}

function formatOffsetTimestampDate(value: string) {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})T/);

  if (match) {
    return `${match[3]}/${match[2]}/${match[1]}`;
  }

  return new Date(value).toLocaleDateString();
}

function formatOffsetTimestampTime(value: string) {
  const match = value.match(/T(\d{2}):(\d{2})/);

  if (match) {
    return `${match[1]}:${match[2]}`;
  }

  return new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date(value));
}

function formatStatus(author: AuthorsTableRow) {
  if (author.status === "stale") {
    if (isTelegramSignedOff(author.stalePresence)) {
      return "Offline";
    }

    return author.lastReceivedAt ? "Offline" : "No reports";
  }

  return "Online";
}

function statusBadgeClassName(status?: "online" | "stale", stalePresence?: AuthorsTableRow["stalePresence"]) {
  if (status === "stale") {
    if (isTelegramSignedOff(stalePresence)) {
      return "status-badge telegram-signed-off";
    }

    return "status-badge stale";
  }

  return "status-badge online";
}

function isTelegramSignedOff(stalePresence?: AuthorsTableRow["stalePresence"]) {
  return stalePresence === "telegram" || stalePresence === "both";
}

function productivityClassName(author: AuthorsTableRow) {
  const value = author.productivity;
  const measuredSeconds =
    author.activeSeconds +
    author.idleSeconds +
    author.meetingSeconds +
    author.overtimeActiveSeconds +
    author.breakSeconds;

  if (value <= 0 && measuredSeconds <= 0) {
    return "metric-value neutral";
  }

  if (value > 100) {
    return "metric-value overdrive";
  }

  if (value > 80) {
    return "metric-value good";
  }

  if (value > 60) {
    return "metric-value warning";
  }

  return "metric-value bad";
}

function breakClassName(seconds: number) {
  if (seconds <= 0) {
    return "metric-value neutral";
  }

  return seconds > 61 * 60 ? "metric-value bad" : "metric-value good";
}
