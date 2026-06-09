import { useEffect, useState } from "react";
import {
  activityAuthorSlugForRawAuthor,
  rawAuthorForActivityAuthorSlug,
  readActivityAuthorSlugFromUrl,
  writeActivityAuthorSlugToUrl
} from "../utils/activityAuthorUrl";
import type { AuthorRow, Page } from "../types/dashboard";

export function useActivityAuthorSelection(page: Page | null, authors: AuthorRow[]) {
  const initialSlug = page === "activity" ? readActivityAuthorSlugFromUrl() : null;
  const initialLookup = rawAuthorForActivityAuthorSlug(authors, initialSlug);
  const initialAuthor = initialLookup.rawAuthor;
  const initialLastSlug = initialAuthor ? initialSlug : null;
  const [selectedAuthor, setSelectedAuthorState] = useState<string | null>(() => initialAuthor);
  const [lastSelectedActivityAuthor, setLastSelectedActivityAuthor] = useState<string | null>(() => initialAuthor);
  const [lastSelectedActivityAuthorSlug, setLastSelectedActivityAuthorSlug] = useState<string | null>(() => initialLastSlug);
  const [selectedAuthorSlug, setSelectedAuthorSlug] = useState<string | null>(() => initialSlug);
  const [authorSlugAmbiguous, setAuthorSlugAmbiguous] = useState(() => page === "activity" && initialLookup.ambiguous);

  useEffect(() => {
    function syncSelectedAuthorFromUrl() {
      if (page !== "activity") {
        setSelectedAuthorState(null);
        setSelectedAuthorSlug(null);
        setAuthorSlugAmbiguous(false);
        return;
      }

      const urlSlug = readActivityAuthorSlugFromUrl();
      const lookup = rawAuthorForActivityAuthorSlug(authors, urlSlug);
      setSelectedAuthorState(lookup.rawAuthor);
      setSelectedAuthorSlug(urlSlug);
      setAuthorSlugAmbiguous(lookup.ambiguous);

      if (lookup.rawAuthor) {
        setLastSelectedActivityAuthor(lookup.rawAuthor);
        setLastSelectedActivityAuthorSlug(urlSlug);
      }
    }

    window.addEventListener("popstate", syncSelectedAuthorFromUrl);

    return () => {
      window.removeEventListener("popstate", syncSelectedAuthorFromUrl);
    };
  }, [authors, page]);

  useEffect(() => {
    if (page !== "activity") {
      setSelectedAuthorState(null);
      setSelectedAuthorSlug(null);
      setAuthorSlugAmbiguous(false);
      return;
    }

    const urlSlug = readActivityAuthorSlugFromUrl();
    const lookup = rawAuthorForActivityAuthorSlug(authors, urlSlug);
    setSelectedAuthorSlug(urlSlug);
    setAuthorSlugAmbiguous(lookup.ambiguous);

    if (selectedAuthor === lookup.rawAuthor) {
      if (lookup.rawAuthor) {
        setLastSelectedActivityAuthor(lookup.rawAuthor);
        setLastSelectedActivityAuthorSlug(urlSlug);
      }
      return;
    }

    setSelectedAuthorState(lookup.rawAuthor);

    if (lookup.rawAuthor) {
      setLastSelectedActivityAuthor(lookup.rawAuthor);
      setLastSelectedActivityAuthorSlug(urlSlug);
    }
  }, [authors, page, selectedAuthor]);

  function setSelectedAuthor(value: string) {
    const slug = activityAuthorSlugForRawAuthor(authors, value);

    if (!slug) {
      return;
    }

    setSelectedAuthorState(value);
    setLastSelectedActivityAuthor(value);
    setLastSelectedActivityAuthorSlug(slug);
    setSelectedAuthorSlug(slug);
    setAuthorSlugAmbiguous(false);
    writeActivityAuthorSlugToUrl(slug);
  }

  const authorSelectionError = selectedAuthorSlug && !selectedAuthor
    ? authorSlugAmbiguous
      ? "Selected author URL is ambiguous."
      : "Selected author is not available in the current activity data."
    : null;

  return { selectedAuthor, lastSelectedActivityAuthor, lastSelectedActivityAuthorSlug, authorSelectionError, setSelectedAuthor };
}
