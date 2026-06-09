import { useEffect, useState } from "react";
import {
  readActivityAuthorFromUrl,
  writeActivityAuthorToUrl
} from "../utils/dashboardStorage";
import type { Page } from "../types/dashboard";

export function useActivityAuthorSelection(page: Page | null) {
  const initialActivityAuthor = page === "activity" ? readActivityAuthorFromUrl() : null;
  const [selectedAuthor, setSelectedAuthorState] = useState<string | null>(() => initialActivityAuthor);
  const [lastSelectedActivityAuthor, setLastSelectedActivityAuthor] = useState<string | null>(() => initialActivityAuthor);

  useEffect(() => {
    function syncSelectedAuthorFromUrl() {
      if (page !== "activity") {
        setSelectedAuthorState(null);
        return;
      }

      const urlAuthor = readActivityAuthorFromUrl();
      setSelectedAuthorState(urlAuthor);

      if (urlAuthor) {
        setLastSelectedActivityAuthor(urlAuthor);
      }
    }

    window.addEventListener("popstate", syncSelectedAuthorFromUrl);

    return () => {
      window.removeEventListener("popstate", syncSelectedAuthorFromUrl);
    };
  }, [page]);

  useEffect(() => {
    if (page !== "activity") {
      setSelectedAuthorState(null);
      return;
    }

    const urlAuthor = readActivityAuthorFromUrl();

    if (selectedAuthor === urlAuthor) {
      if (urlAuthor) {
        setLastSelectedActivityAuthor(urlAuthor);
      }
      return;
    }

    setSelectedAuthorState(urlAuthor);

    if (urlAuthor) {
      setLastSelectedActivityAuthor(urlAuthor);
    }
  }, [page, selectedAuthor]);

  function setSelectedAuthor(value: string) {
    setSelectedAuthorState(value);
    setLastSelectedActivityAuthor(value);
    writeActivityAuthorToUrl(value);
  }

  return { selectedAuthor, lastSelectedActivityAuthor, setSelectedAuthor };
}
