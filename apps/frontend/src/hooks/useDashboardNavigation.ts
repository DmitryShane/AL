import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { PAGE_STORAGE_KEY } from "../constants/dashboard";
import {
  getSavedPageScroll,
  loadSavedPage,
  savePageScroll
} from "../utils/dashboardStorage";
import type { Page } from "../types/dashboard";

export function useDashboardNavigation({
  canRestoreScroll
}: {
  canRestoreScroll: boolean;
}) {
  const [page, setPage] = useState<Page>(() => loadSavedPage());
  const [isRestoringScroll, setIsRestoringScroll] = useState(() => getSavedPageScroll(loadSavedPage()) > 0);
  const isRestoringScrollRef = useRef(isRestoringScroll);
  const skipNextScrollRestoreRef = useRef(false);

  useEffect(() => {
    isRestoringScrollRef.current = isRestoringScroll;
  }, [isRestoringScroll]);

  useLayoutEffect(() => {
    const previousScrollRestoration = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";

    return () => {
      window.history.scrollRestoration = previousScrollRestoration;
    };
  }, []);

  useEffect(() => {
    const saveCurrentPageScroll = (force = false) => {
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
    if (!canRestoreScroll) {
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
    localStorage.setItem(PAGE_STORAGE_KEY, nextPage);

    if (shouldResetScroll) {
      window.requestAnimationFrame(() => {
        window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      });
    }
  }

  return { page, selectPage, isRestoringScroll };
}
