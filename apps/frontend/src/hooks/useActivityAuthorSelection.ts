import { useEffect, useState } from "react";
import {
  readActivityAuthorFromUrl,
  writeActivityAuthorToUrl
} from "../utils/dashboardStorage";
import type { Page } from "../types/dashboard";

export function useActivityAuthorSelection(page: Page | null) {
  const [selectedAuthor, setSelectedAuthorState] = useState<string | null>(() => page === "activity" ? readActivityAuthorFromUrl() : null);

  useEffect(() => {
    function syncSelectedAuthorFromUrl() {
      setSelectedAuthorState(page === "activity" ? readActivityAuthorFromUrl() : null);
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
      return;
    }

    setSelectedAuthorState(urlAuthor);
  }, [page, selectedAuthor]);

  function setSelectedAuthor(value: string) {
    setSelectedAuthorState(value);
    writeActivityAuthorToUrl(value);
  }

  return { selectedAuthor, setSelectedAuthor };
}
