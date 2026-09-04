import type { AuthorRow } from "../../types/dashboard";
import { breakClassName, breakTone } from "../../utils/author";
import { DurationCard } from "./DurationCard";
import { ProductivityCard } from "./ProductivityCard";

type ActivityMetricsGridProps = {
  author?: AuthorRow | null;
  message?: string;
};

export function ActivityMetricsGrid({ author, message }: ActivityMetricsGridProps) {
  return (
    <div className="activity-grid" data-doc-target="activity-metrics" id="activity-metrics">
      <DurationCard message={message} variant="telegram" label="Day Time (Telegram)" seconds={author?.telegramDaySeconds ?? author?.daySeconds} />
      <DurationCard message={message} variant="telegram-delta" label="Telegram vs FirstActivity" seconds={author ? author.telegramToFirstActivitySeconds ?? 0 : undefined} />
      <DurationCard message={message} variant="plugin" label="Day Time (Plugin)" seconds={author ? author.rawPluginDaySeconds ?? author.pluginDaySeconds ?? author.activeSeconds + author.idleSeconds : undefined} />
      <DurationCard message={message} variant="active" label="Active" seconds={author?.activeSeconds} />
      <DurationCard message={message} variant="idle" label="Idle" seconds={author?.idleSeconds} />
      <DurationCard message={message} variant="overtime" label="Overtime" seconds={author?.overtimeActiveSeconds} />
      <DurationCard message={message}
        variant="break"
        label={author?.autoBreakEnabled ? "Break (auto)" : "Break"}
        seconds={author?.breakSeconds}
        className={`break-duration ${author ? breakTone(author.breakSeconds) : "neutral"}`}
        valueClassName={breakClassName(author?.breakSeconds ?? 0)}
      />
      <ProductivityCard message={message} value={author?.productivity} />
    </div>
  );
}
