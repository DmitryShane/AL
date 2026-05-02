import type { AuthorAlert, AlertStats } from "../../types/dashboard";
export function AlertSummaryMetric({ label, value, tone }: { label: string; value: number; tone: "critical" | "warning" | "healthy" | "neutral" }) {
  return (
    <div className={`alert-summary-metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

