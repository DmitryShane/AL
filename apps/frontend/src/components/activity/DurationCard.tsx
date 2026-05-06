import { formatDuration } from "../../utils/format";

type DurationCardProps = {
  label: string;
  seconds: number;
  variant?: "telegram" | "telegram-delta" | "plugin" | "active" | "idle" | "overtime" | "break";
  className?: string;
  valueClassName?: string;
};

export function DurationCard({ label, seconds, variant, className, valueClassName }: DurationCardProps) {
  const classNames = ["duration"];

  if (variant) {
    classNames.push(`duration-${variant}`);
  }

  if (variant === "overtime" && seconds > 0) {
    classNames.push("has-value");
  }

  if (className) {
    classNames.push(className);
  }

  return (
    <div className={classNames.join(" ")}>
      <span>{label}</span>
      <strong className={valueClassName}>{formatDuration(seconds)}</strong>
    </div>
  );
}
