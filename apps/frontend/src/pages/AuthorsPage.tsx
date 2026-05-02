import { RefreshCw, Search } from "lucide-react";
import { AuthorsTable } from "../components/AuthorsTable";
import type { AuthorRow } from "../types/dashboard";
import { compareAuthorsByStatusAndProductivity } from "./pageHelpers";
export function AuthorsPage({
  authors,
  search,
  setSearch,
  refreshing,
  onRefresh
}: {
  authors: AuthorRow[];
  search: string;
  setSearch: (value: string) => void;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const sortedAuthors = [...authors].sort(compareAuthorsByStatusAndProductivity);

  return (
    <section className="page-section">
      <div className="toolbar">
        <div className="search-box">
          <Search size={18} />
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search authors" />
        </div>
        <div className="toolbar-spacer" />
        <button className="primary-outline-button" onClick={onRefresh} disabled={refreshing}>
          <RefreshCw size={16} />
          {refreshing ? "Requesting..." : "Refresh"}
        </button>
      </div>

      <AuthorsTable authors={sortedAuthors} emptyMessage="No authors match this search." />
    </section>
  );
}

