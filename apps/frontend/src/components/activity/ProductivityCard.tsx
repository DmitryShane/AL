import { productivityClassName, productivityTone } from "../../utils/author";

type ProductivityCardProps = {
  value?: number;
  message?: string;
};

export function ProductivityCard({ value, message }: ProductivityCardProps) {
  const productivity = value !== undefined && Number.isFinite(value) ? value : 0;

  return (
    <div className={`duration productivity-duration ${value === undefined ? "" : productivityTone(productivity)}`}>
      <span>Productivity</span>
      <strong className={value === undefined ? "activity-data-message" : productivityClassName(productivity)}>{value === undefined ? message ?? "Loading data…" : `${productivity.toFixed(2)}%`}</strong>
    </div>
  );
}
