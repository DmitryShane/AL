import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { DateRangePicker } from "../layout/DateRangePicker";
import type { AuthorRow, DateRange } from "../../types/dashboard";
import { compareAuthorCardStatus, hasAuthorActivity } from "../../pages/pageHelpers";
import { ActivityAuthorMiniCard } from "./ActivityAuthorMiniCard";

const ACTIVITY_FLOATING_AUTHORS_CACHE_PREFIX = "AL.Dashboard.ActivityFloatingAuthors.";
const ACTIVITY_FLOATING_STRIP_EXIT_MS = 220;

type ActivityAuthorFloatingStripProps = {
  anchorRef: RefObject<HTMLDivElement>;
  authors: AuthorRow[];
  dateRange: DateRange;
  datePickerValue: DateRange;
  onDatePickerChange: (range: DateRange) => void;
  selectedAuthor: string | null;
  setSelectedAuthor: (value: string) => void;
  loading: boolean;
  restoringScroll: boolean;
};

export function ActivityAuthorFloatingStrip({
  anchorRef,
  authors,
  dateRange,
  datePickerValue,
  onDatePickerChange,
  selectedAuthor,
  setSelectedAuthor,
  loading,
  restoringScroll
}: ActivityAuthorFloatingStripProps) {
  const cardAuthors = useMemo(
    () => [...authors].sort((left, right) => compareAuthorCardStatus(left, right, dateRange)),
    [authors, dateRange]
  );
  const floatingAuthorsCacheKey = useMemo(() => JSON.stringify({
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    dateMode: dateRange.preset === "live" ? "authorLocalToday" : ""
  }), [dateRange.startDate, dateRange.endDate, dateRange.preset]);
  const [cachedFloatingAuthors, setCachedFloatingAuthors] = useState<AuthorRow[]>(() => loadCachedFloatingAuthors(floatingAuthorsCacheKey));
  const floatingAuthors = loading && cachedFloatingAuthors.length ? cachedFloatingAuthors : cardAuthors;
  const shouldRestoreFloatingStrip = restoringScroll && floatingAuthors.length > 0;
  const [anchorInView, setAnchorInView] = useState(!shouldRestoreFloatingStrip);
  const shouldShowFloatingStrip = !anchorInView && floatingAuthors.length > 0;
  const [mounted, setMounted] = useState(shouldRestoreFloatingStrip);
  const [exiting, setExiting] = useState(false);
  const [animKey, setAnimKey] = useState(0);
  const exitTimerRef = useRef<number | null>(null);
  const prevShouldShowRef = useRef(shouldRestoreFloatingStrip);
  const [restored] = useState(shouldRestoreFloatingStrip);

  useEffect(() => {
    setCachedFloatingAuthors(loadCachedFloatingAuthors(floatingAuthorsCacheKey));
  }, [floatingAuthorsCacheKey]);

  useEffect(() => {
    if (loading || !cardAuthors.length || !cardAuthors.some(hasAuthorActivity)) {
      return;
    }

    setCachedFloatingAuthors(cardAuthors);
    saveCachedFloatingAuthors(floatingAuthorsCacheKey, cardAuthors);
  }, [cardAuthors, floatingAuthorsCacheKey, loading]);

  useEffect(() => {
    const element = anchorRef.current;

    if (!element) {
      return;
    }

    const intersectionObserver = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];

        if (entry) {
          setAnchorInView(entry.isIntersecting);
        }
      },
      { threshold: 0 }
    );

    intersectionObserver.observe(element);

    return () => {
      intersectionObserver.disconnect();
    };
  }, [anchorRef]);

  useEffect(() => {
    if (shouldShowFloatingStrip && !prevShouldShowRef.current) {
      setAnimKey((key) => key + 1);
    }

    prevShouldShowRef.current = shouldShowFloatingStrip;
  }, [shouldShowFloatingStrip]);

  useEffect(() => {
    if (shouldShowFloatingStrip) {
      if (exitTimerRef.current !== null) {
        window.clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }

      setExiting(false);
      setMounted(true);
      return;
    }

    if (!mounted) {
      return;
    }

    setExiting(true);
    exitTimerRef.current = window.setTimeout(() => {
      exitTimerRef.current = null;
      setMounted(false);
      setExiting(false);
    }, ACTIVITY_FLOATING_STRIP_EXIT_MS);

    return () => {
      if (exitTimerRef.current !== null) {
        window.clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
    };
  }, [shouldShowFloatingStrip, mounted]);

  if (!mounted) {
    return null;
  }

  return (
    <div
      key={animKey}
      className={`activity-author-floating-strip ${restored ? "is-restored " : ""}${exiting ? "is-exiting" : ""}`.trim()}
      role="region"
      aria-label="Authors and date range"
    >
      <div className="activity-author-floating-strip-inner">
        <div className="activity-author-floating-strip-scroll">
          {floatingAuthors.map((item) => (
            <ActivityAuthorMiniCard
              key={`float-${item.rawAuthor}`}
              author={item}
              active={item.rawAuthor === selectedAuthor}
              onSelect={(selected) => setSelectedAuthor(selected.rawAuthor)}
            />
          ))}
        </div>
        <div className="activity-author-floating-strip-dates">
          <DateRangePicker value={datePickerValue} onChange={onDatePickerChange} />
        </div>
      </div>
    </div>
  );
}

function activityFloatingCacheKey(key: string) {
  return `${ACTIVITY_FLOATING_AUTHORS_CACHE_PREFIX}${key}`;
}

function loadCachedFloatingAuthors(key: string) {
  try {
    const cached = sessionStorage.getItem(activityFloatingCacheKey(key));

    if (!cached) {
      return [];
    }

    return JSON.parse(cached) as AuthorRow[];
  } catch {
    return [];
  }
}

function saveCachedFloatingAuthors(key: string, authors: AuthorRow[]) {
  try {
    sessionStorage.setItem(activityFloatingCacheKey(key), JSON.stringify(authors));
  } catch {
    // Ignore storage failures; the main dashboard summary remains the source of truth.
  }
}
