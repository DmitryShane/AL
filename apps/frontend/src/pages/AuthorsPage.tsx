import { Search } from "lucide-react";
import { AuthorsTable } from "../components/AuthorsTable";
import type { AuthorRow } from "../types/dashboard";
import { compareAuthorsByStatusAndProductivity } from "./pageHelpers";
export function AuthorsPage({
  authors,
  loading,
  search,
  setSearch
}: {
  authors: AuthorRow[];
  loading: boolean;
  search: string;
  setSearch: (value: string) => void;
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
      </div>

      <AuthorsTable authors={sortedAuthors} emptyMessage={loading ? "Loading authors..." : "No authors match this search."} />
    </section>
  );
}

