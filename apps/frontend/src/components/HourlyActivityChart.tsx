type HourlyActivity = {
  hour: number;
  activeSeconds: number;
  idleSeconds: number;
  breakSeconds?: number;
  breakSegments?: Array<{ startSecond: number; endSecond: number }>;
  meetingSeconds?: number;
  overtimeActiveSeconds?: number;
  overtimeFillSeconds?: number;
  missedSeconds?: number;
  missedStartSeconds?: number;
  missedEndSeconds?: number;
};

type AuthorHourlyActivity = {
  author: string;
  status?: "online" | "stale";
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  hourlyActivity: HourlyActivity[];
};

type AuthorHourlyChart = {
  author: string;
  timeZoneLabel?: string;
  hours: Array<{
    hour: number;
    activeMinutes: number;
    breakMinutes: number;
    meetingMinutes: number;
    idleMinutes: number;
    overtimeMinutes: number;
    overtimeFillMinutes: number;
    missedMinutes: number;
    missedStartMinutes: number;
    missedEndMinutes: number;
    breakSegments: Array<{ startPercent: number; heightPercent: number }>;
    isInProgress: boolean;
  }>;
};

export type HourlyActivityChartProps = {
  authors: AuthorHourlyActivity[];
};

export function HourlyActivityChart({ authors }: HourlyActivityChartProps) {
  const authorCharts = authors.map(toAuthorHourlyActivity);

  return (
    <section className="panel table-panel">
      <h2>Hourly Activity</h2>
      {authorCharts.length ? (
        <div className="hourly-chart-list">
          {authorCharts.map((author) => (
            <article className="hourly-chart-card" key={author.author}>
              <div className="hourly-chart-scroll">
                <div className="hourly-chart">
                  <div className="hourly-chart-y-axis">
                    <span>60</span>
                    <span>45</span>
                    <span>30</span>
                    <span>15</span>
                    <span>0</span>
                  </div>
                  <div className="hourly-chart-bars">
                    {author.hours.map((hour) => {
                      const segments = toDisplaySegments(hour);
                      const barClassName = [
                        "hourly-chart-bar",
                        hasHourlyActivity(hour) ? "has-activity" : "",
                        hour.isInProgress ? "is-current-hour" : ""
                      ].filter(Boolean).join(" ");

                      return (
                        <div className="hourly-chart-column" key={hour.hour}>
                          <div
                            className={barClassName}
                            title={formatHourTitle(hour)}
                          >
                            <div
                              className="hourly-chart-segment missed"
                              style={{ height: `${segments.missedStartPercent}%` }}
                            />
                            <div
                              className="hourly-chart-segment active"
                              style={{ height: `${segments.activePercent}%` }}
                            />
                            <div
                              className="hourly-chart-segment overtime"
                              style={{ height: `${segments.overtimePercent}%` }}
                            />
                            <div
                              className="hourly-chart-segment overtime-fill"
                              style={{ height: `${segments.overtimeFillPercent}%` }}
                            />
                            {hour.breakSegments.length ? (
                              hour.breakSegments.map((segment, index) => (
                                <div
                                  className="hourly-chart-segment break is-positioned"
                                  key={`${segment.startPercent}-${segment.heightPercent}-${index}`}
                                  style={{ bottom: `${segment.startPercent}%`, height: `${segment.heightPercent}%` }}
                                />
                              ))
                            ) : (
                              <div
                                className="hourly-chart-segment break"
                                style={{ height: `${segments.breakPercent}%` }}
                              />
                            )}
                            <div
                              className="hourly-chart-segment meeting"
                              style={{ height: `${segments.meetingPercent}%` }}
                            />
                            <div
                              className="hourly-chart-segment idle"
                              style={{ height: `${segments.idlePercent}%` }}
                            />
                            <div
                              className="hourly-chart-segment missed"
                              style={{ height: `${segments.missedEndPercent}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <div />
                  <div className="hourly-chart-x-axis">
                    {author.hours.map((hour) => (
                      <span className={hasHourlyActivity(hour) ? "has-activity" : undefined} key={hour.hour}>
                        <span>{formatHour(hour.hour)}</span>
                      </span>
                    ))}
                  </div>
                </div>
              </div>
              <div className="hourly-chart-legend">
                <span><i className="active" />Active</span>
                <span><i className="overtime" />Overtime</span>
                <span><i className="break" />AFK</span>
                <span><i className="meeting" />Meeting</span>
                <span><i className="idle" />Idle</span>
                <span><i className="missed" />Missed</span>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className="empty">No authors yet.</p>
      )}
    </section>
  );
}

function createEmptyHourlyActivity(): Required<HourlyActivity>[] {
  return Array.from({ length: 24 }, (_, hour) => ({
    hour,
    activeSeconds: 0,
    idleSeconds: 0,
    breakSeconds: 0,
    meetingSeconds: 0,
    overtimeActiveSeconds: 0,
    overtimeFillSeconds: 0,
    missedSeconds: 0,
    missedStartSeconds: 0,
    missedEndSeconds: 0,
    breakSegments: []
  }));
}

function normalizeHourlyActivity(source: HourlyActivity[]) {
  const hourlyActivity = createEmptyHourlyActivity();

  for (const sourceHour of source) {
    if (sourceHour.hour < 0 || sourceHour.hour >= hourlyActivity.length) {
      continue;
    }

    hourlyActivity[sourceHour.hour].activeSeconds = sourceHour.activeSeconds ?? 0;
    hourlyActivity[sourceHour.hour].idleSeconds = sourceHour.idleSeconds ?? 0;
    hourlyActivity[sourceHour.hour].breakSeconds = sourceHour.breakSeconds ?? 0;
    hourlyActivity[sourceHour.hour].meetingSeconds = sourceHour.meetingSeconds ?? 0;
    hourlyActivity[sourceHour.hour].overtimeActiveSeconds = sourceHour.overtimeActiveSeconds ?? 0;
    hourlyActivity[sourceHour.hour].overtimeFillSeconds = sourceHour.overtimeFillSeconds ?? 0;
    hourlyActivity[sourceHour.hour].missedSeconds = sourceHour.missedSeconds ?? 0;
    hourlyActivity[sourceHour.hour].missedStartSeconds = sourceHour.missedStartSeconds ?? 0;
    hourlyActivity[sourceHour.hour].missedEndSeconds = sourceHour.missedEndSeconds ?? 0;
    hourlyActivity[sourceHour.hour].breakSegments = normalizeBreakSegments(sourceHour.breakSegments);
  }

  return hourlyActivity;
}

function toAuthorHourlyActivity(author: AuthorHourlyActivity): AuthorHourlyChart {
  const hourlyActivity = normalizeHourlyActivity(author.hourlyActivity?.length ? author.hourlyActivity : createEmptyHourlyActivity());
  const inProgressHour = author.status === "stale" ? null : findInProgressHour(hourlyActivity);

  return {
    author: author.author,
    timeZoneLabel: formatTimeZoneLabel(author),
    hours: hourlyActivity.map((hour) => ({
      hour: hour.hour,
      activeMinutes: Math.min(60, hour.activeSeconds / 60),
      breakMinutes: Math.min(60, (hour.breakSeconds ?? 0) / 60),
      meetingMinutes: Math.min(60, (hour.meetingSeconds ?? 0) / 60),
      idleMinutes: Math.min(60, hour.idleSeconds / 60),
      overtimeMinutes: Math.min(60, (hour.overtimeActiveSeconds ?? 0) / 60),
      overtimeFillMinutes: Math.min(60, (hour.overtimeFillSeconds ?? 0) / 60),
      missedMinutes: Math.min(60, (hour.missedSeconds ?? 0) / 60),
      missedStartMinutes: Math.min(60, (hour.missedStartSeconds ?? 0) / 60),
      missedEndMinutes: Math.min(60, (hour.missedEndSeconds ?? 0) / 60),
      breakSegments: toPositionedBreakSegments(hour.breakSegments),
      isInProgress: hour.hour === inProgressHour
    }))
  };
}

function toPercentOfHour(minutes: number) {
  return Math.min(100, Math.max(0, (minutes / 60) * 100));
}

function normalizeBreakSegments(segments: HourlyActivity["breakSegments"]) {
  if (!Array.isArray(segments)) {
    return [];
  }

  return segments.flatMap((segment) => {
    const startSecond = Math.min(3600, Math.max(0, segment.startSecond ?? 0));
    const endSecond = Math.min(3600, Math.max(0, segment.endSecond ?? 0));

    if (endSecond <= startSecond) {
      return [];
    }

    return [{ startSecond, endSecond }];
  });
}

function toPositionedBreakSegments(segments: Array<{ startSecond: number; endSecond: number }>) {
  return segments.map((segment) => ({
    startPercent: (segment.startSecond / 3600) * 100,
    heightPercent: ((segment.endSecond - segment.startSecond) / 3600) * 100
  }));
}

function toDisplaySegments(hour: { activeMinutes: number; breakMinutes: number; meetingMinutes: number; idleMinutes: number; overtimeMinutes: number; overtimeFillMinutes: number; missedStartMinutes: number; missedEndMinutes: number }) {
  const activeMinutes = Math.max(0, hour.activeMinutes);
  const overtimeMinutes = Math.max(0, hour.overtimeMinutes);
  const overtimeFillMinutes = Math.max(0, hour.overtimeFillMinutes);
  const breakMinutes = Math.max(0, hour.breakMinutes);
  const meetingMinutes = Math.max(0, hour.meetingMinutes);
  const idleMinutes = Math.max(0, hour.idleMinutes);
  const missedStartMinutes = Math.max(0, hour.missedStartMinutes);
  const missedEndMinutes = Math.max(0, hour.missedEndMinutes);
  const totalMinutes = activeMinutes + overtimeMinutes + overtimeFillMinutes + breakMinutes + meetingMinutes + idleMinutes + missedStartMinutes + missedEndMinutes;

  if (totalMinutes <= 0) {
    return { activePercent: 0, overtimePercent: 0, overtimeFillPercent: 0, breakPercent: 0, meetingPercent: 0, idlePercent: 0, missedStartPercent: 0, missedEndPercent: 0 };
  }

  return {
    activePercent: toPercentOfHour(activeMinutes),
    overtimePercent: toPercentOfHour(overtimeMinutes),
    overtimeFillPercent: toPercentOfHour(overtimeFillMinutes),
    breakPercent: toPercentOfHour(breakMinutes),
    meetingPercent: toPercentOfHour(meetingMinutes),
    idlePercent: toPercentOfHour(idleMinutes),
    missedStartPercent: toPercentOfHour(missedStartMinutes),
    missedEndPercent: toPercentOfHour(missedEndMinutes)
  };
}

function hasHourlyActivity(hour: { activeMinutes: number; breakMinutes: number; meetingMinutes: number; idleMinutes: number; overtimeMinutes: number; overtimeFillMinutes: number; missedMinutes: number }) {
  return hour.activeMinutes > 0 || hour.overtimeMinutes > 0 || hour.overtimeFillMinutes > 0 || hour.breakMinutes > 0 || hour.meetingMinutes > 0 || hour.idleMinutes > 0 || hour.missedMinutes > 0;
}

function findInProgressHour(hours: Required<HourlyActivity>[]) {
  for (let index = hours.length - 1; index >= 0; index -= 1) {
    const hour = hours[index];
    const totalSeconds = hour.activeSeconds + hour.overtimeActiveSeconds + hour.overtimeFillSeconds + hour.breakSeconds + hour.meetingSeconds + hour.idleSeconds;

    if (totalSeconds > 0 && totalSeconds < 3600) {
      return hour.hour;
    }
  }

  return null;
}

function formatHour(hour: number) {
  const displayHour = String(hour).padStart(2, "0");

  return `${displayHour}:00`;
}

function formatHourTitle(hour: { hour: number; activeMinutes: number; breakMinutes: number; meetingMinutes: number; idleMinutes: number; overtimeMinutes: number; missedMinutes: number }) {
  return `${formatHour(hour.hour)}: ${Math.round(hour.activeMinutes)}m active, ${Math.round(hour.overtimeMinutes)}m overtime, ${Math.round(hour.breakMinutes)}m AFK, ${Math.round(hour.meetingMinutes)}m meeting, ${Math.round(hour.idleMinutes)}m idle, ${Math.round(hour.missedMinutes)}m missed`;
}

function formatTimeZoneLabel(author: AuthorHourlyActivity) {
  if (author.timeZoneId) {
    const city = author.timeZoneId.split("/").pop()?.replace(/_/g, " ");

    if (city) {
      return city;
    }
  }

  return author.timeZoneDisplayName;
}
