import type { AuthorRow } from "../../types/dashboard";
import { breakClassName, breakTone } from "../../utils/author";
import { DurationCard } from "./DurationCard";
import { ProductivityCard } from "./ProductivityCard";

type ActivityMetricsGridProps = {
  author: AuthorRow;
};

export function ActivityMetricsGrid({ author }: ActivityMetricsGridProps) {
  return (
    <div className="activity-grid">
      <DurationCard variant="telegram" label="Day Time (Telegram)" seconds={author.telegramDaySeconds ?? author.daySeconds} />
      <DurationCard variant="telegram-delta" label="Telegram vs FirstActivity" seconds={author.telegramToFirstActivitySeconds ?? 0} />
      <DurationCard variant="plugin" label="Day Time (Plugin)" seconds={author.rawPluginDaySeconds ?? author.pluginDaySeconds ?? author.activeSeconds + author.idleSeconds} />
      <DurationCard variant="active" label="Active" seconds={author.activeSeconds} />
      <DurationCard variant="idle" label="Idle" seconds={author.idleSeconds} />
      <DurationCard variant="overtime" label="Overtime" seconds={author.overtimeActiveSeconds} />
      <DurationCard
        variant="break"
        label={author.autoBreakEnabled ? "Break (auto)" : "Break"}
        seconds={author.breakSeconds}
        className={`break-duration ${breakTone(author.breakSeconds)}`}
        valueClassName={breakClassName(author.breakSeconds)}
      />
      <ProductivityCard value={author.productivity} />
    </div>
  );
}
