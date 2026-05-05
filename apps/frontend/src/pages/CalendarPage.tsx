import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api/client";
import { CALENDAR_SUMMARY_CACHE_KEY } from "../constants/dashboard";
import type { CalendarMark, CalendarSummary } from "../types/dashboard";
import { AuthorAvatar } from "../components/AuthorAvatar";
import { dateRangeList, monthIndexes, uniqueDates } from "./pageHelpers";
import { CalendarClearEditor, CalendarLegend, CalendarMarkEditor, CalendarStats, MonthCalendar, ReasonEditor } from "../components/calendar/CalendarComponents";
export function CalendarPage() {
  const year = new Date().getFullYear();
  const [calendar, setCalendar] = useState<CalendarSummary | null>(() => loadCachedCalendarSummary(year));
  const [selectedAuthor, setSelectedAuthor] = useState("all");
  const [selectedDates, setSelectedDates] = useState<string[]>([]);
  const [rangeStart, setRangeStart] = useState<string | null>(null);
  const [rangeMode, setRangeMode] = useState(false);
  const [showMarkEditor, setShowMarkEditor] = useState(false);
  const [showClearEditor, setShowClearEditor] = useState(false);
  const [markAuthors, setMarkAuthors] = useState<string[]>([]);
  const [clearAuthors, setClearAuthors] = useState<string[]>([]);
  const [markReason, setMarkReason] = useState("");
  const [markNote, setMarkNote] = useState("");
  const [reasonEditId, setReasonEditId] = useState<string | null>(null);
  const [reasonLabel, setReasonLabel] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadCalendar(showLoading = true) {
    if (showLoading) {
      setLoading(true);
    }

    try {
      const response = await apiFetch(`/api/v1/calendar/summary?year=${year}`);

      if (!response.ok) {
        throw new Error("Calendar request failed");
      }

      const data: CalendarSummary = await response.json();
      setCalendar(data);
      saveCachedCalendarSummary(data);
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load calendar.");
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    void loadCalendar();
    const intervalId = window.setInterval(() => void loadCalendar(false), 5 * 60 * 1000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, []);

  const visibleMarks = useMemo(
    () => (calendar?.marks ?? []).filter((mark) => selectedAuthor === "all" || mark.rawAuthor === selectedAuthor),
    [calendar?.marks, selectedAuthor]
  );
  const visibleStats = useMemo(
    () => (calendar?.stats ?? []).filter((stat) => selectedAuthor === "all" || stat.rawAuthor === selectedAuthor),
    [calendar?.stats, selectedAuthor]
  );
  const marksByDate = useMemo(
    () => visibleMarks.reduce<Record<string, CalendarMark[]>>((items, mark) => {
      items[mark.date] = [...(items[mark.date] ?? []), mark];
      return items;
    }, {}),
    [visibleMarks]
  );

  function toggleDate(date: string, shiftKey = false) {
    if ((rangeMode || shiftKey) && rangeStart) {
      setSelectedDates(uniqueDates([...selectedDates, ...dateRangeList(rangeStart, date)]));
      setRangeStart(null);
      return;
    }

    if (rangeMode || shiftKey) {
      setRangeStart(date);
      setSelectedDates(uniqueDates([...selectedDates, date]));
      return;
    }

    setSelectedDates((items) => (items.includes(date) ? items.filter((item) => item !== date) : [...items, date].sort()));
  }

  function openMarkEditor() {
    if (!calendar || !selectedDates.length) {
      return;
    }

    setMarkAuthors(selectedAuthor === "all" ? [] : [selectedAuthor]);
    setMarkReason("");
    setMarkNote("");
    setShowMarkEditor(true);
  }

  function openMarkEditorForReason(reasonId: string) {
    if (!calendar || !selectedDates.length) {
      setError("Select day or days before applying a reason.");
      return;
    }

    setMarkAuthors(selectedAuthor === "all" ? [] : [selectedAuthor]);
    setMarkReason(reasonId);
    setMarkNote("");
    setShowMarkEditor(true);
    setError(null);
  }

  function openClearEditor() {
    if (!calendar || !selectedDates.length) {
      return;
    }

    setClearAuthors(selectedAuthor === "all" ? [] : [selectedAuthor]);
    setShowClearEditor(true);
    setError(null);
  }

  async function saveMarks() {
    if (!markAuthors.length || !selectedDates.length || !markReason) {
      setError("Select authors, dates, and reason.");
      return;
    }

    const response = await apiFetch(`/api/v1/calendar/marks`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ authors: markAuthors, dates: selectedDates, reasonId: markReason, note: markNote.trim() })
    });

    if (!response.ok) {
      setError("Calendar mark save failed.");
      return;
    }

    setShowMarkEditor(false);
    setMarkReason("");
    setSelectedDates([]);
    await loadCalendar(false);
  }

  async function clearMarks() {
    if (!clearAuthors.length || !selectedDates.length) {
      setError("Select authors and dates to clear.");
      return;
    }

    const response = await apiFetch(`/api/v1/calendar/marks/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ authors: clearAuthors, dates: selectedDates })
    });

    if (!response.ok) {
      setError("Calendar mark delete failed.");
      return;
    }

    setShowClearEditor(false);
    setSelectedDates([]);
    await loadCalendar(false);
  }

  async function saveReason() {
    if (!reasonLabel.trim()) {
      return;
    }

    const response = await apiFetch(`/api/v1/calendar/reasons`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: reasonEditId, label: reasonLabel.trim() })
    });

    if (!response.ok) {
      setError("Reason save failed.");
      return;
    }

    setReasonLabel("");
    setReasonEditId(null);
    await loadCalendar(false);
  }

  return (
    <section className="page-section calendar-page">
      {error ? <p className="notice error">{error}</p> : null}
      {calendar ? (
        <>
          <div className="calendar-year-header">
            <div>
              <span>Current date</span>
              <strong>{new Date().toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" })}</strong>
            </div>
            <div>
              <span>Calendar year</span>
              <strong>{calendar.year}</strong>
            </div>
          </div>

          <div className="author-card-strip calendar-author-strip">
            <button className={selectedAuthor === "all" ? "author-card active" : "author-card"} onClick={() => setSelectedAuthor("all")}>
              <span className="avatar-stack" aria-hidden="true">
                {calendar.authors.slice(0, 5).map((author) => (
                  <AuthorAvatar
                    variant="mini"
                    displayName={author.displayName}
                    authorColor={author.authorColor}
                    avatarUrl={author.avatarUrl}
                    key={author.rawAuthor}
                  />
                ))}
              </span>
              <strong>All authors</strong>
              <small>Show all marks</small>
            </button>
            {calendar.authors.map((author) => (
              <button className={selectedAuthor === author.rawAuthor ? "author-card active" : "author-card"} key={author.rawAuthor} onClick={() => setSelectedAuthor(author.rawAuthor)}>
                <AuthorAvatar displayName={author.displayName} authorColor={author.authorColor} avatarUrl={author.avatarUrl} />
                <strong>{author.displayName}</strong>
                <small>{author.team || "No team"}</small>
              </button>
            ))}
          </div>

          <div className="calendar-workspace">
            <div className="calendar-sidebar">
              <div className="calendar-management">
                <div className="calendar-toolbar">
                  <strong>{selectedDates.length} selected days</strong>
                  <button className={rangeMode ? "primary-button" : "primary-outline-button"} onClick={() => setRangeMode((value) => !value)}>Range select</button>
                  <button className="primary-outline-button" onClick={() => { setSelectedDates([]); setRangeStart(null); }}>Clear selection</button>
                  <button className="primary-button" onClick={openMarkEditor} disabled={!selectedDates.length}>Mark days</button>
                  <button className="primary-outline-button danger-button" onClick={openClearEditor} disabled={!selectedDates.length}>Clear marks</button>
                </div>
                <ReasonEditor
                  reasons={calendar.reasons}
                  reasonLabel={reasonLabel}
                  setReasonLabel={setReasonLabel}
                  setReasonEditId={setReasonEditId}
                  onPickReason={openMarkEditorForReason}
                  onSave={() => void saveReason()}
                />
              </div>

              <CalendarStats stats={visibleStats} reasons={calendar.reasons} />
            </div>

            <div className="year-calendar-panel">
              <div className="year-calendar">
                {monthIndexes().map((month) => (
                  <MonthCalendar
                    year={year}
                    month={month}
                    marksByDate={marksByDate}
                    selectedDates={selectedDates}
                    rangeStart={rangeStart}
                    onSelect={toggleDate}
                    key={month}
                  />
                ))}
              </div>
              <CalendarLegend authors={selectedAuthor === "all" ? calendar.authors : calendar.authors.filter((author) => author.rawAuthor === selectedAuthor)} reasons={calendar.reasons} />
            </div>
          </div>

          {showMarkEditor ? (
            <CalendarMarkEditor
              authors={calendar.authors}
              selectedAuthor={selectedAuthor}
              selectedAuthors={markAuthors}
              setSelectedAuthors={setMarkAuthors}
              reasons={calendar.reasons}
              reasonId={markReason}
              setReasonId={setMarkReason}
              note={markNote}
              setNote={setMarkNote}
              selectedDates={selectedDates}
              onCancel={() => setShowMarkEditor(false)}
              onSave={() => void saveMarks()}
            />
          ) : null}
          {showClearEditor ? (
            <CalendarClearEditor
              authors={calendar.authors}
              selectedAuthor={selectedAuthor}
              selectedAuthors={clearAuthors}
              setSelectedAuthors={setClearAuthors}
              selectedDates={selectedDates}
              onCancel={() => setShowClearEditor(false)}
              onClear={() => void clearMarks()}
            />
          ) : null}
        </>
      ) : loading ? (
        <p className="notice">Loading calendar...</p>
      ) : (
        <p className="empty">No calendar data yet.</p>
      )}
    </section>
  );
}

function loadCachedCalendarSummary(year: number) {
  try {
    const cached = sessionStorage.getItem(calendarCacheKey(year));

    if (!cached) {
      return null;
    }

    return JSON.parse(cached) as CalendarSummary;
  } catch {
    return null;
  }
}

function saveCachedCalendarSummary(summary: CalendarSummary) {
  try {
    sessionStorage.setItem(calendarCacheKey(summary.year), JSON.stringify(summary));
  } catch {
    // Ignore storage failures; live API data is still shown.
  }
}

function calendarCacheKey(year: number) {
  return `${CALENDAR_SUMMARY_CACHE_KEY}.${year}`;
}

