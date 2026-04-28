type HourlyActivity = {
  hour: number;
  activeSeconds: number;
  idleSeconds: number;
};

type AuthorHourlyActivity = {
  author: string;
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
    idleMinutes: number;
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
              <div className="hourly-chart-header">
                <div>
                  <strong>{author.author}</strong>
                  <span>
                    Today
                    {author.timeZoneLabel ? `, ${author.timeZoneLabel}` : ""}
                  </span>
                </div>
              </div>
              <div className="hourly-chart-scroll">
                <div className="hourly-chart">
                  <div className="hourly-chart-y-title">Per hour, min</div>
                  <div className="hourly-chart-y-axis">
                    <span>60</span>
                    <span>45</span>
                    <span>30</span>
                    <span>15</span>
                    <span>0</span>
                  </div>
                  <div className="hourly-chart-bars">
                    {author.hours.map((hour) => (
                      <div className="hourly-chart-column" key={hour.hour}>
                        <div
                          className={`hourly-chart-bar${hasHourlyActivity(hour) ? " has-activity" : ""}`}
                          title={formatHourTitle(hour)}
                        >
                          <div
                            className="hourly-chart-segment active"
                            style={{ height: `${toPercentOfHour(hour.activeMinutes)}%` }}
                          />
                          <div
                            className="hourly-chart-segment idle"
                            style={{ height: `${toPercentOfHour(hour.idleMinutes)}%` }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                  <div />
                  <div />
                  <div className="hourly-chart-x-axis">
                    {author.hours.map((hour) => (
                      <span key={hour.hour}>
                        <span>{formatHour(hour.hour)}</span>
                      </span>
                    ))}
                  </div>
                </div>
              </div>
              <div className="hourly-chart-legend">
                <span><i className="active" />Active</span>
                <span><i className="idle" />Idle</span>
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

function createEmptyHourlyActivity() {
  return Array.from({ length: 24 }, (_, hour) => ({ hour, activeSeconds: 0, idleSeconds: 0 }));
}

function normalizeHourlyActivity(source: HourlyActivity[]) {
  const hourlyActivity = createEmptyHourlyActivity();

  for (const sourceHour of source) {
    if (sourceHour.hour < 0 || sourceHour.hour >= hourlyActivity.length) {
      continue;
    }

    hourlyActivity[sourceHour.hour].activeSeconds = sourceHour.activeSeconds ?? 0;
    hourlyActivity[sourceHour.hour].idleSeconds = sourceHour.idleSeconds ?? 0;
  }

  return hourlyActivity;
}

function toAuthorHourlyActivity(author: AuthorHourlyActivity): AuthorHourlyChart {
  const hourlyActivity = normalizeHourlyActivity(author.hourlyActivity?.length ? author.hourlyActivity : createEmptyHourlyActivity());

  return {
    author: author.author,
    timeZoneLabel: formatTimeZoneLabel(author),
    hours: hourlyActivity.map((hour) => ({
      hour: hour.hour,
      activeMinutes: Math.min(60, hour.activeSeconds / 60),
      idleMinutes: Math.min(60, hour.idleSeconds / 60)
    }))
  };
}

function toPercentOfHour(minutes: number) {
  return Math.min(100, Math.max(0, (minutes / 60) * 100));
}

function hasHourlyActivity(hour: { activeMinutes: number; idleMinutes: number }) {
  return hour.activeMinutes > 0 || hour.idleMinutes > 0;
}

function formatHour(hour: number) {
  const displayHour = String(hour + 1).padStart(2, "0");

  return `${displayHour}:00`;
}

function formatHourTitle(hour: { hour: number; activeMinutes: number; idleMinutes: number }) {
  return `${formatHour(hour.hour)}: ${Math.round(hour.activeMinutes)}m active, ${Math.round(hour.idleMinutes)}m idle`;
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
