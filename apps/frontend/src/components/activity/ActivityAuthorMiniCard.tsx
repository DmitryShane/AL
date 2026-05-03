import type { AuthorRow } from "../../types/dashboard";
import { authorCardProductivityTone, authorMiniCardClassName } from "../../pages/pageHelpers";
import { AuthorAvatar } from "../AuthorAvatar";

type ActivityAuthorMiniCardProps = {
  author: AuthorRow;
  active: boolean;
  onSelect: (author: AuthorRow) => void;
};

export function ActivityAuthorMiniCard({ author, active, onSelect }: ActivityAuthorMiniCardProps) {
  return (
    <button
      type="button"
      className={authorMiniCardClassName(author, active)}
      onClick={() => onSelect(author)}
    >
      <span className="author-mini-card-avatar-wrap">
        <AuthorAvatar displayName={author.displayName} authorColor={author.authorColor} avatarUrl={author.avatarUrl} variant="mini" />
        <span className="author-mini-card-status" aria-hidden="true" />
      </span>
      <span className="author-mini-card-name">{author.displayName}</span>
      <span className={`productivity-badge author-mini-card-productivity ${authorCardProductivityTone(author)}`}>
        <strong>{author.productivity.toFixed(0)}%</strong>
      </span>
    </button>
  );
}
