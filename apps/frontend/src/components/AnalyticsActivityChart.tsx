export type AnalyticsActivityTotals = {
  activeSeconds: number;
  idleSeconds: number;
  breakSeconds: number;
  meetingSeconds?: number;
  overtimeActiveSeconds: number;
  productivity: number;
};

export type AnalyticsActivityBar = {
  id: string;
  label: string;
  title: string;
  totals: AnalyticsActivityTotals;
  maxSeconds?: number;
};

type AnalyticsActivityChartProps = {
  title: string;
  subtitle?: string;
  productivity: number;
  bars: AnalyticsActivityBar[];
  className?: string;
};

export function AnalyticsActivityChart({ title, subtitle, productivity, bars, className }: AnalyticsActivityChartProps) {
  const maxSeconds = Math.max(1, ...bars.map((bar) => bar.maxSeconds ?? chartableSeconds(bar.totals)));
  const chartClassName = ["analytics-activity-chart-card", className].filter(Boolean).join(" ");

  return (
    <article className={chartClassName}>
      <div className="analytics-activity-chart-header">
        <div>
          <h3>{title}</h3>
          {subtitle ? <span>{subtitle}</span> : null}
        </div>
        <strong className="analytics-productivity-badge">{productivity.toFixed(0)}%</strong>
      </div>

      <div className="analytics-activity-chart">
        <div className="analytics-activity-y-axis">
          <span>{formatAxisValue(maxSeconds)}</span>
          <span>{formatAxisValue(maxSeconds * 0.75)}</span>
          <span>{formatAxisValue(maxSeconds * 0.5)}</span>
          <span>{formatAxisValue(maxSeconds * 0.25)}</span>
          <span>0</span>
        </div>
        <div className="analytics-activity-bars" style={{ gridTemplateColumns: `repeat(${Math.max(1, bars.length)}, minmax(0, 1fr))` }}>
          {bars.map((bar) => {
            const segments = toSegments(bar.totals, maxSeconds);
            const hasActivity = chartableSeconds(bar.totals) > 0;
            const barClassName = ["analytics-activity-bar", hasActivity ? "has-activity" : ""].filter(Boolean).join(" ");

            return (
              <div className="analytics-activity-column" key={bar.id}>
                <div className={barClassName} title={bar.title}>
                  <div className="analytics-activity-segment active" style={{ height: `${segments.activePercent}%` }} />
                  <div className="analytics-activity-segment overtime" style={{ height: `${segments.overtimePercent}%` }} />
                  <div className="analytics-activity-segment break" style={{ height: `${segments.breakPercent}%` }} />
                  <div className="analytics-activity-segment meeting" style={{ height: `${segments.meetingPercent}%` }} />
                  <div className="analytics-activity-segment idle" style={{ height: `${segments.idlePercent}%` }} />
                </div>
              </div>
            );
          })}
        </div>
        <div />
        <div className="analytics-activity-x-axis" style={{ gridTemplateColumns: `repeat(${Math.max(1, bars.length)}, minmax(0, 1fr))` }}>
          {bars.map((bar) => (
            <span className={chartableSeconds(bar.totals) > 0 ? "has-activity" : undefined} key={bar.id}>
              <span>{bar.label}</span>
            </span>
          ))}
        </div>
      </div>
    </article>
  );
}

export function AnalyticsActivityLegend() {
  return (
    <div className="analytics-activity-legend">
      <span><i className="active" />Active</span>
      <span><i className="overtime" />Overtime</span>
      <span><i className="break" />AFK</span>
      <span><i className="meeting" />Meeting</span>
      <span><i className="idle" />Idle</span>
    </div>
  );
}

function toSegments(totals: AnalyticsActivityTotals, maxSeconds: number) {
  return {
    activePercent: toPercent(totals.activeSeconds, maxSeconds),
    overtimePercent: toPercent(totals.overtimeActiveSeconds, maxSeconds),
    breakPercent: toPercent(totals.breakSeconds, maxSeconds),
    meetingPercent: toPercent(totals.meetingSeconds ?? 0, maxSeconds),
    idlePercent: toPercent(totals.idleSeconds, maxSeconds)
  };
}

function toPercent(seconds: number, maxSeconds: number) {
  return Math.min(100, Math.max(0, (seconds / maxSeconds) * 100));
}

function chartableSeconds(totals: AnalyticsActivityTotals) {
  return totals.activeSeconds + totals.overtimeActiveSeconds + totals.breakSeconds + (totals.meetingSeconds ?? 0) + totals.idleSeconds;
}

function formatAxisValue(seconds: number) {
  if (seconds >= 3600) {
    return `${Math.round(seconds / 3600)}h`;
  }

  return `${Math.round(seconds / 60)}m`;
}
