import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { DateRangePicker } from "../layout/DateRangePicker";
import type { AuthorRow, DateRange } from "../../types/dashboard";
import { compareAuthorCardStatus } from "../../pages/pageHelpers";
import { ActivityAuthorMiniCard } from "./ActivityAuthorMiniCard";

const ACTIVITY_FLOATING_STRIP_EXIT_MS = 220;

type ActivityAuthorFloatingStripProps = {
  anchorRef: RefObject<HTMLDivElement | null>;
  authors: AuthorRow[];
  dateRange: DateRange;
  datePickerValue: DateRange;
  onDatePickerChange: (range: DateRange) => void;
  selectedAuthor: string | null;
  setSelectedAuthor: (value: string) => void;
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
  restoringScroll
}: ActivityAuthorFloatingStripProps) {
  const cardAuthors = useMemo(
    () => [...authors].sort((left, right) => compareAuthorCardStatus(left, right, dateRange)),
    [authors, dateRange]
  );
  const shouldRestoreFloatingStrip = restoringScroll && cardAuthors.length > 0;
  const shouldMountRestored = shouldRestoreFloatingStrip;
  const [anchorMeasured, setAnchorMeasured] = useState(shouldMountRestored);
  const [anchorInView, setAnchorInView] = useState(!shouldMountRestored);
  const shouldShowFloatingStrip = anchorMeasured && !anchorInView && cardAuthors.length > 0;
  const [mounted, setMounted] = useState(shouldMountRestored);
  const [exiting, setExiting] = useState(false);
  const [animKey, setAnimKey] = useState(0);
  const exitTimerRef = useRef<number | null>(null);
  const prevShouldShowRef = useRef(false);
  const [restored] = useState(shouldRestoreFloatingStrip);

  useLayoutEffect(() => {
    const element = anchorRef.current;

    if (!element) {
      return;
    }

    const rect = element.getBoundingClientRect();
    setAnchorMeasured(true);
    setAnchorInView(rect.bottom > 0 && rect.top < window.innerHeight);
  }, [anchorRef]);

  useEffect(() => {
    if (!restoringScroll) {
      return;
    }

    const frameId = window.requestAnimationFrame(() => {
      const element = anchorRef.current;

      if (!element) {
        return;
      }

      const rect = element.getBoundingClientRect();
      setAnchorMeasured(true);
      setAnchorInView(rect.bottom > 0 && rect.top < window.innerHeight);
    });

    return () => {
      window.cancelAnimationFrame(frameId);
    };
  }, [anchorRef, restoringScroll]);

  useEffect(() => {
    const element = anchorRef.current;

    if (!element) {
      setAnchorMeasured(false);
      return;
    }

    setAnchorMeasured(false);

    const intersectionObserver = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];

        if (entry) {
          setAnchorMeasured(true);
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
          {cardAuthors.map((item) => (
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

