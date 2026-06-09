import { useEffect, useState } from "react";
import { ACTIVITY_AUTHOR_STORAGE_KEY } from "../constants/dashboard";
import {
  loadSavedActivityAuthor,
  readActivityAuthorFromUrl,
  writeActivityAuthorToUrl
} from "../utils/dashboardStorage";
import type { Page } from "../types/dashboard";

export function useActivityAuthorSelection(page: Page) {
  const [selectedAuthor, setSelectedAuthorState] = useState<string | null>(() => loadSavedActivityAuthor());

  useEffect(() => {
    function syncSelectedAuthorFromUrl() {
      const urlAuthor = readActivityAuthorFromUrl();
      setSelectedAuthorState(urlAuthor);

      if (urlAuthor) {
        localStorage.setItem(ACTIVITY_AUTHOR_STORAGE_KEY, urlAuthor);
      }
    }

    window.addEventListener("popstate", syncSelectedAuthorFromUrl);

    return () => {
      window.removeEventListener("popstate", syncSelectedAuthorFromUrl);
    };
  }, []);

  useEffect(() => {
    if (page !== "activity" || !selectedAuthor || readActivityAuthorFromUrl()) {
      return;
    }

    writeActivityAuthorToUrl(selectedAuthor);
  }, [page, selectedAuthor]);

  function setSelectedAuthor(value: string) {
    setSelectedAuthorState(value);
    localStorage.setItem(ACTIVITY_AUTHOR_STORAGE_KEY, value);
    writeActivityAuthorToUrl(value);
  }

  return { selectedAuthor, setSelectedAuthor };
}
