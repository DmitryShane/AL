import type React from "react";
import { AnalyticsActivityLegend } from "./AnalyticsActivityChart";
import { AnalyticsMonthActivityChart, monthHasActivity, type AnalyticsMonthActivity } from "./AnalyticsMonthActivityChart";

export type AnalyticsActivityAuthor = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  authorColor?: string;
  months: AnalyticsMonthActivity[];
};

type AnalyticsActivityOverviewProps = {
  author: AnalyticsActivityAuthor;
  year: number;
  avatar: React.ReactNode;
};

export function AnalyticsActivityOverview({ author, year, avatar }: AnalyticsActivityOverviewProps) {
  const monthsWithData = author.months.filter(monthHasActivity);

  return (
    <section className="analytics-activity-overview">
      <div className="analytics-selected-author">
        {avatar}
        <div>
          <strong>{author.displayName}</strong>
          <small title={author.authorEmail || author.rawAuthor}>{author.authorEmail || author.rawAuthor}</small>
          <small>{author.team || "No team"} · {year}</small>
        </div>
      </div>

      <AnalyticsActivityLegend />

      <div className="analytics-month-chart-list">
        {monthsWithData.length ? (
          monthsWithData.map((month) => <AnalyticsMonthActivityChart key={month.month} month={month} year={year} />)
        ) : (
          <p className="empty">No analytics activity for this author.</p>
        )}
      </div>
    </section>
  );
}
