import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  getSavedPageScroll,
  savePageScroll
} from "../utils/dashboardStorage";
import { normalizeDashboardPath, pageToPath, pathToPage } from "../utils/dashboardRoutes";
import type { Page } from "../types/dashboard";

export function useDashboardNavigation({
  canRestoreScroll
}: {
  canRestoreScroll: boolean;
}) {
  const initialPage = readPageFromLocation();
  const [page, setPage] = useState<Page | null>(() => initialPage);
  const [isRestoringScroll, setIsRestoringScroll] = useState(() => initialPage ? getSavedPageScroll(initialPage) > 0 : false);
  const isRestoringScrollRef = useRef(isRestoringScroll);
  const skipNextScrollRestoreRef = useRef(false);

  useEffect(() => {
    isRestoringScrollRef.current = isRestoringScroll;
  }, [isRestoringScroll]);

  useLayoutEffect(() => {
    const previousScrollRestoration = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    replaceNonCanonicalPath();

    return () => {
      window.history.scrollRestoration = previousScrollRestoration;
    };
  }, []);

  useEffect(() => {
    function syncPageFromLocation() {
      replaceNonCanonicalPath();
      setPage(readPageFromLocation());
    }

    window.addEventListener("popstate", syncPageFromLocation);

    return () => {
      window.removeEventListener("popstate", syncPageFromLocation);
    };
  }, []);

  useEffect(() => {
    const saveCurrentPageScroll = (force = false) => {
      if (!page) {
        return;
      }

      if (!force && isRestoringScrollRef.current) {
        return;
      }

      savePageScroll(page, window.scrollY);
    };
    const savePageScrollOnScroll = () => saveCurrentPageScroll();
    const savePageScrollBeforeUnload = () => saveCurrentPageScroll(true);

    window.addEventListener("scroll", savePageScrollOnScroll, { passive: true });
    window.addEventListener("beforeunload", savePageScrollBeforeUnload);

    return () => {
      saveCurrentPageScroll(true);
      window.removeEventListener("scroll", savePageScrollOnScroll);
      window.removeEventListener("beforeunload", savePageScrollBeforeUnload);
    };
  }, [page]);

  useLayoutEffect(() => {
    if (!canRestoreScroll || !page) {
      return;
    }

    if (skipNextScrollRestoreRef.current) {
      skipNextScrollRestoreRef.current = false;
      setIsRestoringScroll(false);
      return;
    }

    const savedScroll = getSavedPageScroll(page);

    if (savedScroll <= 0) {
      setIsRestoringScroll(false);
      return;
    }

    window.scrollTo({ top: savedScroll, left: 0, behavior: "auto" });

    window.requestAnimationFrame(() => {
      setIsRestoringScroll(false);
    });
  }, [canRestoreScroll, page]);

  function selectPage(nextPage: Page) {
    const shouldResetScroll = nextPage !== page;

    if (shouldResetScroll) {
      skipNextScrollRestoreRef.current = true;
      setIsRestoringScroll(false);
      savePageScroll(nextPage, 0);
    }

    setPage(nextPage);
    window.history.pushState(window.history.state, "", pageToPath(nextPage));

    if (shouldResetScroll) {
      window.requestAnimationFrame(() => {
        window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      });
    }
  }

  return { page, selectPage, isRestoringScroll };
}

function readPageFromLocation() {
  if (window.location.pathname === "/") {
    return "authors";
  }

  return pathToPage(window.location.pathname);
}

function replaceRootPathWithAuthors() {
  if (window.location.pathname !== "/") {
    return;
  }

  window.history.replaceState(window.history.state, "", pageToPath("authors"));
}

function replaceNonCanonicalPath() {
  if (window.location.pathname === "/") {
    replaceRootPathWithAuthors();
    return;
  }

  const normalized = normalizeDashboardPath(window.location.pathname);

  if (normalized === window.location.pathname || !pathToPage(normalized)) {
    return;
  }

  window.history.replaceState(window.history.state, "", `${normalized}${window.location.search}${window.location.hash}`);
}
