import { AnalyticsActivityChart, type AnalyticsActivityBar, type AnalyticsActivityTotals } from "./AnalyticsActivityChart";
import { AnalyticsWeekActivityChart, type AnalyticsWeekActivity, weekHasActivity } from "./AnalyticsWeekActivityChart";

export type AnalyticsMonthActivity = {
  month: number;
  label: string;
  startDate: string;
  endDate: string;
  totals: AnalyticsActivityTotals;
  previousMonthDeltas?: unknown;
  weeks: AnalyticsWeekActivity[];
};

type AnalyticsMonthActivityChartProps = {
  month: AnalyticsMonthActivity;
  year: number;
};

export function AnalyticsMonthActivityChart({ month, year }: AnalyticsMonthActivityChartProps) {
  const activeWeeks = month.weeks.filter(weekHasActivity);

  return (
    <section className="analytics-month-activity-card">
      <AnalyticsActivityChart
        title={month.label}
        subtitle={String(year)}
        productivity={month.totals.productivity}
        bars={activeWeeks.map(toWeekBar)}
        className="analytics-month-summary-chart"
      />

      <div className="analytics-week-chart-list">
        {activeWeeks.length ? (
          activeWeeks.map((week) => <AnalyticsWeekActivityChart key={`${month.month}-${week.week}`} week={week} />)
        ) : (
          <p className="empty">No activity data for this month.</p>
        )}
      </div>
    </section>
  );
}

export function monthHasActivity(month: AnalyticsMonthActivity) {
  return hasTotalsActivity(month.totals) || month.weeks.some(weekHasActivity);
}

function toWeekBar(week: AnalyticsWeekActivity): AnalyticsActivityBar {
  return {
    id: `${week.startDate}-${week.endDate}`,
    label: `W${week.week}`,
    title: `${week.label}: ${formatDuration(week.totals.activeSeconds)} active, ${formatDuration(week.totals.overtimeActiveSeconds)} overtime, ${formatDuration(week.totals.breakSeconds)} AFK, ${formatDuration(week.totals.meetingSeconds ?? 0)} meeting, ${formatDuration(week.totals.idleSeconds)} idle, ${week.totals.productivity.toFixed(0)}% productivity`,
    totals: week.totals
  };
}

function hasTotalsActivity(totals: AnalyticsActivityTotals) {
  return totals.activeSeconds > 0
    || totals.idleSeconds > 0
    || totals.breakSeconds > 0
    || (totals.meetingSeconds ?? 0) > 0
    || totals.overtimeActiveSeconds > 0;
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
