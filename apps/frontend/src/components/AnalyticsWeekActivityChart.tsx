import { AnalyticsActivityChart, type AnalyticsActivityBar, type AnalyticsActivityTotals } from "./AnalyticsActivityChart";
import { AnalyticsDayActivityChart, type AnalyticsDayActivity } from "./AnalyticsDayActivityChart";

export type AnalyticsWeekActivity = {
  week: number;
  label: string;
  startDate: string;
  endDate: string;
  totals: AnalyticsActivityTotals;
  previousWeekDeltas?: unknown;
  days: AnalyticsDayActivity[];
};

type AnalyticsWeekActivityChartProps = {
  week: AnalyticsWeekActivity;
};

export function AnalyticsWeekActivityChart({ week }: AnalyticsWeekActivityChartProps) {
  const workDays = week.days.filter((day) => day.inMonth && isWorkDay(day.date));

  return (
    <section className="analytics-week-activity-card">
      <AnalyticsActivityChart
        title={`Week ${week.week}`}
        subtitle={week.label}
        productivity={week.totals.productivity}
        bars={workDays.map(toDayBar)}
        className="analytics-week-summary-chart"
      />

      <div className="analytics-day-chart-grid">
        {workDays.length ? (
          workDays.map((day) => <AnalyticsDayActivityChart day={day} key={day.date} />)
        ) : (
          <p className="empty">No workday activity for this week.</p>
        )}
      </div>
    </section>
  );
}

export function weekHasActivity(week: AnalyticsWeekActivity) {
  return hasTotalsActivity(week.totals) || week.days.some((day) => day.inMonth && hasTotalsActivity(day.totals));
}

function toDayBar(day: AnalyticsDayActivity): AnalyticsActivityBar {
  return {
    id: day.date,
    label: formatDayLabel(day.date),
    title: `${formatDayTitle(day.date)}: ${formatDuration(day.totals.activeSeconds)} active, ${formatDuration(day.totals.overtimeActiveSeconds)} overtime, ${formatDuration(day.totals.breakSeconds)} AFK, ${formatDuration(day.totals.meetingSeconds ?? 0)} meeting, ${formatDuration(day.totals.idleSeconds)} idle, ${day.totals.productivity.toFixed(0)}% productivity`,
    totals: day.totals
  };
}

function isWorkDay(date: string) {
  const day = new Date(`${date}T00:00:00`).getDay();

  return day >= 1 && day <= 5;
}

function hasTotalsActivity(totals: AnalyticsActivityTotals) {
  return totals.activeSeconds > 0
    || totals.idleSeconds > 0
    || totals.breakSeconds > 0
    || (totals.meetingSeconds ?? 0) > 0
    || totals.overtimeActiveSeconds > 0;
}

function formatDayLabel(date: string) {
  const parsed = new Date(`${date}T00:00:00`);

  return parsed.toLocaleDateString("en-US", { weekday: "short", day: "numeric" });
}

function formatDayTitle(date: string) {
  const parsed = new Date(`${date}T00:00:00`);

  return parsed.toLocaleDateString("en-US", { day: "numeric", month: "short" });
}

function formatDuration(seconds: number) {
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours <= 0) {
    return `${minutes}m`;
  }

  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}
