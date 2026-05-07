type FillKind = "active" | "overtime" | "overtime-fill" | "afk" | "auto-afk" | "meeting" | "telegram-idle" | "idle" | "missed";

type HourlyFillTotals = {
  activeSeconds: number;
  overtimeSeconds: number;
  afkSeconds: number;
  meetingSeconds: number;
  idleSeconds: number;
  missedSeconds: number;
};

type HourlyFillSegment = {
  kind: FillKind;
  startSecond: number;
  endSecond: number;
};

type HourlyActivity = {
  hour: number;
  totals: HourlyFillTotals;
  fillSegments: HourlyFillSegment[];
};

type AuthorHourlyActivity = {
  author: string;
  status?: "online" | "stale";
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  hourlyActivity: HourlyActivity[];
};

export type HourlyActivityChartProps = {
  authors: AuthorHourlyActivity[];
};

const FILL_KINDS: FillKind[] = ["active", "overtime", "overtime-fill", "afk", "auto-afk", "meeting", "telegram-idle", "idle", "missed"];

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
                      const barClassName = [
                        "hourly-chart-bar",
                        hasHourlyActivity(hour) ? "has-activity" : "",
                        hour.isInProgress ? "is-current-hour" : ""
                      ].filter(Boolean).join(" ");

                      return (
                        <div className="hourly-chart-column" key={hour.hour}>
                          <div className={barClassName} title={formatHourTitle(hour)}>
                            {hour.fillSegments.map((segment, index) => (
                              <div
                                className={`hourly-chart-segment ${toSegmentClassName(segment.kind)}`}
                                key={`${segment.kind}-${segment.startSecond}-${segment.endSecond}-${index}`}
                                style={{
                                  bottom: `${toPercentOfHour(segment.startSecond)}%`,
                                  height: `${toPercentOfHour(segment.endSecond - segment.startSecond)}%`
                                }}
                              />
                            ))}
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
                <span><i className="afk" />AFK</span>
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

function createEmptyHourlyActivity(): HourlyActivity[] {
  return Array.from({ length: 24 }, (_, hour) => ({
    hour,
    totals: {
      activeSeconds: 0,
      overtimeSeconds: 0,
      afkSeconds: 0,
      meetingSeconds: 0,
      idleSeconds: 0,
      missedSeconds: 0
    },
    fillSegments: []
  }));
}

function toAuthorHourlyActivity(author: AuthorHourlyActivity) {
  const hourlyActivity = normalizeHourlyActivity(author.hourlyActivity?.length ? author.hourlyActivity : createEmptyHourlyActivity());
  const inProgressHour = author.status === "stale" ? null : findInProgressHour(hourlyActivity);

  return {
    author: author.author,
    timeZoneLabel: formatTimeZoneLabel(author),
    hours: hourlyActivity.map((hour) => ({
      ...hour,
      isInProgress: hour.hour === inProgressHour
    }))
  };
}

function normalizeHourlyActivity(source: HourlyActivity[]) {
  const hourlyActivity = createEmptyHourlyActivity();

  for (const sourceHour of source) {
    if (sourceHour.hour < 0 || sourceHour.hour >= hourlyActivity.length) {
      continue;
    }

    hourlyActivity[sourceHour.hour] = {
      hour: sourceHour.hour,
      totals: normalizeTotals(sourceHour.totals),
      fillSegments: normalizeFillSegments(sourceHour.fillSegments)
    };
  }

  return hourlyActivity;
}

function normalizeTotals(source: Partial<HourlyFillTotals> | undefined): HourlyFillTotals {
  return {
    activeSeconds: Math.max(0, source?.activeSeconds ?? 0),
    overtimeSeconds: Math.max(0, source?.overtimeSeconds ?? 0),
    afkSeconds: Math.max(0, source?.afkSeconds ?? 0),
    meetingSeconds: Math.max(0, source?.meetingSeconds ?? 0),
    idleSeconds: Math.max(0, source?.idleSeconds ?? 0),
    missedSeconds: Math.max(0, source?.missedSeconds ?? 0)
  };
}

function normalizeFillSegments(source: HourlyFillSegment[] | undefined) {
  if (!Array.isArray(source)) {
    return [];
  }

  return source.flatMap((segment) => {
    if (!FILL_KINDS.includes(segment.kind)) {
      return [];
    }

    const startSecond = Math.min(3600, Math.max(0, segment.startSecond ?? 0));
    const endSecond = Math.min(3600, Math.max(0, segment.endSecond ?? 0));

    if (endSecond <= startSecond) {
      return [];
    }

    return [{ kind: segment.kind, startSecond, endSecond }];
  });
}

function toSegmentClassName(kind: FillKind) {
  return kind === "auto-afk" ? "afk" : kind;
}

function toPercentOfHour(seconds: number) {
  return Math.min(100, Math.max(0, (seconds / 3600) * 100));
}

function hasHourlyActivity(hour: HourlyActivity) {
  return Object.values(hour.totals).some((value) => value > 0) || hour.fillSegments.length > 0;
}

function hourlyTooltipBreakdown(hour: HourlyActivity): HourlyFillTotals {
  const fromSegments = hour.fillSegments.reduce(
    (totals, segment) => {
      const seconds = segment.endSecond - segment.startSecond;

      if (segment.kind === "auto-afk") {
        totals.afkSeconds += seconds;
      } else if (segment.kind === "telegram-idle") {
        totals.idleSeconds += seconds;
      } else if (segment.kind === "overtime-fill") {
        return totals;
      } else {
        const key = `${segment.kind}Seconds` as keyof HourlyFillTotals;
        totals[key] += seconds;
      }

      return totals;
    },
    {
      activeSeconds: 0,
      overtimeSeconds: 0,
      afkSeconds: 0,
      meetingSeconds: 0,
      idleSeconds: 0,
      missedSeconds: 0
    }
  );

  return fromSegments;
}

function findInProgressHour(hours: HourlyActivity[]) {
  for (let index = hours.length - 1; index >= 0; index -= 1) {
    const hour = hours[index];
    const totalSeconds = hour.totals.activeSeconds + hour.totals.overtimeSeconds + hour.totals.afkSeconds + hour.totals.meetingSeconds + hour.totals.idleSeconds;

    if (totalSeconds > 0 && totalSeconds < 3600) {
      return hour.hour;
    }
  }

  return null;
}

function formatHourTitle(hour: HourlyActivity) {
  const breakdown = hourlyTooltipBreakdown(hour);

  return `${formatHour(hour.hour)}: ${formatMinutes(breakdown.activeSeconds)} active, ${formatMinutes(breakdown.overtimeSeconds)} overtime, ${formatMinutes(breakdown.afkSeconds)} AFK, ${formatMinutes(breakdown.meetingSeconds)} meeting, ${formatMinutes(breakdown.idleSeconds)} idle, ${formatMinutes(breakdown.missedSeconds)} missed`;
}

function formatHour(hour: number) {
  return `${String(hour).padStart(2, "0")}:00`;
}

function formatMinutes(seconds: number) {
  return `${Math.round(seconds / 60)}m`;
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
