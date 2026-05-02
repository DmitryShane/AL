import type { AuthorRow } from "../../types/dashboard";
import {
  authorCardClassName,
  authorCardProductivityTone,
  avatarStyle,
  formatDuration,
  initials,
  productivityTone
} from "../../pages/pageHelpers";

type ActivityCardProps = {
  author: AuthorRow;
  active: boolean;
  onSelect: (author: AuthorRow) => void;
};

export function ActivityCard({ author, active, onSelect }: ActivityCardProps) {
  return (
    <button
      className={authorCardClassName(author, active)}
      onClick={() => onSelect(author)}
    >
      <span className="author-card-status" aria-hidden="true" />
      <span className="author-card-identity">
        <span className="avatar" style={avatarStyle(author.authorColor)}>{initials(author.displayName)}</span>
        {productivityTone(author.productivity) === "overdrive" ? <span className="overdrive-author-text">Are you human?</span> : null}
      </span>
      <strong>{author.displayName}</strong>
      <small>{author.team || "No team"}</small>
      <div className="author-card-footer">
        <div className="mini-metrics">
          <span>{formatDuration(author.activeSeconds)} active</span>
          <span>{formatDuration(author.idleSeconds)} idle</span>
          <span>{formatDuration(author.breakSeconds)} break</span>
        </div>
        <div className={`productivity-badge ${authorCardProductivityTone(author)}`}>
          <strong>{author.productivity.toFixed(0)}%</strong>
        </div>
      </div>
    </button>
  );
}
