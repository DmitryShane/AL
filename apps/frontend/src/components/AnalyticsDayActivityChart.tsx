import { AnalyticsActivityChart, type AnalyticsActivityBar, type AnalyticsActivityTotals } from "./AnalyticsActivityChart";

export type AnalyticsHourlyActivity = {
  hour: number;
  totals: {
    activeSeconds: number;
    idleSeconds: number;
    afkSeconds: number;
    meetingSeconds: number;
    overtimeSeconds: number;
    missedSeconds: number;
  };
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
      activeSeconds: hour.totals.activeSeconds,
      idleSeconds: hour.totals.idleSeconds,
      breakSeconds: hour.totals.afkSeconds,
      meetingSeconds: hour.totals.meetingSeconds,
      overtimeActiveSeconds: hour.totals.overtimeSeconds,
      productivity: day.totals.productivity
    }
  }));
}

function normalizeHourlyActivity(source: AnalyticsHourlyActivity[]) {
  const hours: AnalyticsHourlyActivity[] = Array.from({ length: 24 }, (_, hour) => ({
    hour,
    totals: {
      activeSeconds: 0,
      idleSeconds: 0,
      afkSeconds: 0,
      meetingSeconds: 0,
      overtimeSeconds: 0,
      missedSeconds: 0
    }
  }));

  for (const sourceHour of source ?? []) {
    if (sourceHour.hour < 0 || sourceHour.hour >= hours.length) {
      continue;
    }

    hours[sourceHour.hour] = {
      hour: sourceHour.hour,
      totals: {
        activeSeconds: sourceHour.totals?.activeSeconds ?? 0,
        idleSeconds: sourceHour.totals?.idleSeconds ?? 0,
        afkSeconds: sourceHour.totals?.afkSeconds ?? 0,
        meetingSeconds: sourceHour.totals?.meetingSeconds ?? 0,
        overtimeSeconds: sourceHour.totals?.overtimeSeconds ?? 0,
        missedSeconds: sourceHour.totals?.missedSeconds ?? 0
      }
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

function formatHourTitle(hour: AnalyticsHourlyActivity) {
  return `${formatHour(hour.hour)}:00: ${formatMinutes(hour.totals.activeSeconds)} active, ${formatMinutes(hour.totals.overtimeSeconds)} overtime, ${formatMinutes(hour.totals.afkSeconds)} AFK, ${formatMinutes(hour.totals.meetingSeconds)} meeting, ${formatMinutes(hour.totals.idleSeconds)} idle`;
}

function formatMinutes(seconds: number) {
  return `${Math.round(seconds / 60)}m`;
}
