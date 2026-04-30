import { AnalyticsActivityChart, type AnalyticsActivityBar, type AnalyticsActivityTotals } from "./AnalyticsActivityChart";

export type AnalyticsHourlyActivity = {
  hour: number;
  activeSeconds: number;
  idleSeconds: number;
  breakSeconds?: number;
  meetingSeconds?: number;
  overtimeActiveSeconds?: number;
};

export type AnalyticsDayActivity = {
  date: string;
  label: string;
  inMonth: boolean;
  totals: AnalyticsActivityTotals;
  hourlyActivity: AnalyticsHourlyActivity[];
};

type AnalyticsDayActivityChartProps = {
  day: AnalyticsDayActivity;
};

export function AnalyticsDayActivityChart({ day }: AnalyticsDayActivityChartProps) {
  return (
    <AnalyticsActivityChart
      title={formatDayTitle(day.date)}
      subtitle="Hourly Activity"
      productivity={day.totals.productivity}
      bars={toHourlyBars(day)}
      className="analytics-day-activity-chart"
    />
  );
}

function toHourlyBars(day: AnalyticsDayActivity): AnalyticsActivityBar[] {
  const hours = normalizeHourlyActivity(day.hourlyActivity);

  return hours.map((hour) => ({
    id: `${day.date}-${hour.hour}`,
    label: formatHour(hour.hour),
    title: formatHourTitle(hour),
    maxSeconds: 3600,
    totals: {
      activeSeconds: hour.activeSeconds,
      idleSeconds: hour.idleSeconds,
      breakSeconds: hour.breakSeconds ?? 0,
      meetingSeconds: hour.meetingSeconds ?? 0,
      overtimeActiveSeconds: hour.overtimeActiveSeconds ?? 0,
      productivity: day.totals.productivity
    }
  }));
}

function normalizeHourlyActivity(source: AnalyticsHourlyActivity[]) {
  const hours = Array.from({ length: 24 }, (_, hour) => ({
    hour,
    activeSeconds: 0,
    idleSeconds: 0,
    breakSeconds: 0,
    meetingSeconds: 0,
    overtimeActiveSeconds: 0
  }));

  for (const sourceHour of source ?? []) {
    if (sourceHour.hour < 0 || sourceHour.hour >= hours.length) {
      continue;
    }

    hours[sourceHour.hour] = {
      hour: sourceHour.hour,
      activeSeconds: sourceHour.activeSeconds ?? 0,
      idleSeconds: sourceHour.idleSeconds ?? 0,
      breakSeconds: sourceHour.breakSeconds ?? 0,
      meetingSeconds: sourceHour.meetingSeconds ?? 0,
      overtimeActiveSeconds: sourceHour.overtimeActiveSeconds ?? 0
    };
  }

  return hours;
}

function formatDayTitle(date: string) {
  const parsed = new Date(`${date}T00:00:00`);

  return parsed.toLocaleDateString("en-US", { day: "numeric", month: "short" });
}

function formatHour(hour: number) {
  return String(hour).padStart(2, "0");
}

function formatHourTitle(hour: Required<AnalyticsHourlyActivity>) {
  return `${formatHour(hour.hour)}:00: ${formatMinutes(hour.activeSeconds)} active, ${formatMinutes(hour.overtimeActiveSeconds)} overtime, ${formatMinutes(hour.breakSeconds)} AFK, ${formatMinutes(hour.meetingSeconds)} meeting, ${formatMinutes(hour.idleSeconds)} idle`;
}

function formatMinutes(seconds: number) {
  return `${Math.round(seconds / 60)}m`;
}
