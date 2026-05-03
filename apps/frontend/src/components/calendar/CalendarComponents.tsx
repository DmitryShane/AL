import React from "react";
import type { CalendarAuthor, CalendarAuthorStats, CalendarMark, CalendarReason } from "../../types/dashboard";
import { calendarDayClassName, formatMinutes, initials, monthIndexes, toCalendarDate, toDateInputValue } from "../../pages/pageHelpers";
export function MonthCalendar({
  year,
  month,
  marksByDate,
  selectedDates,
  rangeStart,
  onSelect
}: {
  year: number;
  month: number;
  marksByDate: Record<string, CalendarMark[]>;
  selectedDates: string[];
  rangeStart: string | null;
  onSelect: (date: string, shiftKey: boolean) => void;
}) {
  const firstDay = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const leadingDays = (firstDay.getDay() + 6) % 7;
  const monthName = firstDay.toLocaleString(undefined, { month: "long" });

  return (
    <section className="month-card">
      <h2>{monthName}</h2>
      <div className="month-weekdays">
        {["M", "T", "W", "T", "F", "S", "S"].map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}
      </div>
      <div className="month-grid">
        {Array.from({ length: leadingDays }).map((_, index) => <span className="calendar-day empty-day" key={`empty-${index}`} />)}
        {Array.from({ length: daysInMonth }).map((_, index) => {
          const day = index + 1;
          const date = toCalendarDate(year, month, day);
          const marks = marksByDate[date] ?? [];
          const selected = selectedDates.includes(date);
          const today = toDateInputValue(new Date());
          const isToday = date === today;
          const isPast = date < today;
          const title = marks.map((mark) => `${mark.displayName}: ${mark.reasonLabel} - ${mark.note}`).join("\n");

          return (
            <button
              className={calendarDayClassName(selected, isToday, isPast)}
              disabled={isPast}
              key={date}
              title={title || (isPast ? `${date} is locked` : date)}
              onClick={(event) => onSelect(date, event.shiftKey)}
            >
              <span>{day}</span>
              {isToday ? <strong>Today</strong> : null}
              {marks.length ? (
                <span className="day-mark-stack">
                  {marks.slice(0, 4).map((mark) => <i style={{ background: mark.authorColor }} key={`${mark.rawAuthor}-${mark.reasonId}`} />)}
                </span>
              ) : null}
              {rangeStart === date ? <span className="range-dot" /> : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}

export function CalendarLegend({ reasons }: { authors: CalendarAuthor[]; reasons: CalendarReason[] }) {
  return (
    <div className="calendar-legend">
      <div className="reason-list">
        {reasons.map((reason) => <span key={reason.id}>{reason.label}</span>)}
      </div>
    </div>
  );
}

export function ReasonEditor({
  reasons,
  reasonLabel,
  setReasonLabel,
  setReasonEditId,
  onPickReason,
  onSave
}: {
  reasons: CalendarReason[];
  reasonLabel: string;
  setReasonLabel: (value: string) => void;
  setReasonEditId: (value: string | null) => void;
  onPickReason: (reasonId: string) => void;
  onSave: () => void;
}) {
  return (
    <div className="calendar-reasons">
      <h2>Reasons</h2>
      <div className="reason-list">
        {reasons.map((reason) => (
          <button
            className="reason-chip-button"
            key={reason.id}
            onClick={() => onPickReason(reason.id)}
            title="Apply this reason to selected days"
          >
            {reason.label}
          </button>
        ))}
      </div>
      <div className="reason-editor">
        <input
          value={reasonLabel}
          onChange={(event) => {
            setReasonEditId(null);
            setReasonLabel(event.target.value);
          }}
          placeholder="New reason label"
        />
        <button className="primary-outline-button" onClick={onSave} disabled={!reasonLabel.trim()}>Save reason</button>
      </div>
      <p className="calendar-helper">Select days, then click a reason chip to mark them.</p>
    </div>
  );
}

export function CalendarStats({ stats, reasons }: { stats: CalendarAuthorStats[]; reasons: CalendarReason[] }) {
  return (
    <div className="calendar-stats-grid">
      {stats.map((stat) => (
        <article className="calendar-stat-card" key={stat.rawAuthor}>
          <div>
            <span className="color-dot" style={{ background: stat.authorColor }} />
            <strong>{stat.displayName}</strong>
          </div>
          <span>{stat.totalMarkedDays} marked days</span>
          <div className="stat-reasons">
            {reasons.map((reason) => <small key={reason.id}>{reason.label}: {stat.byReason[reason.id] ?? 0}</small>)}
          </div>
          <div className="latest-mark-stack">
            {stat.latestMarks.length ? stat.latestMarks.map((mark) => (
              <small key={`${mark.date}-${mark.reasonId}`}>{mark.date}: {mark.reasonLabel}</small>
            )) : <small>No marks yet.</small>}
          </div>
        </article>
      ))}
    </div>
  );
}

export function CalendarMarkEditor({
  authors,
  selectedAuthor,
  selectedAuthors,
  setSelectedAuthors,
  reasons,
  reasonId,
  setReasonId,
  note,
  setNote,
  selectedDates,
  onCancel,
  onSave
}: {
  authors: CalendarAuthor[];
  selectedAuthor: string;
  selectedAuthors: string[];
  setSelectedAuthors: (value: string[]) => void;
  reasons: CalendarReason[];
  reasonId: string;
  setReasonId: (value: string) => void;
  note: string;
  setNote: (value: string) => void;
  selectedDates: string[];
  onCancel: () => void;
  onSave: () => void;
}) {
  function toggleAuthor(rawAuthor: string) {
    setSelectedAuthors(selectedAuthors.includes(rawAuthor) ? selectedAuthors.filter((item) => item !== rawAuthor) : [...selectedAuthors, rawAuthor]);
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div className="calendar-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <h2>Mark {selectedDates.length} days</h2>
        <div className="modal-author-list">
          {authors.map((author) => (
            <label key={author.rawAuthor}>
              <input
                type="checkbox"
                checked={selectedAuthors.includes(author.rawAuthor)}
                disabled={selectedAuthor !== "all" && selectedAuthor !== author.rawAuthor}
                onChange={() => toggleAuthor(author.rawAuthor)}
              />
              <span className="color-dot" style={{ background: author.authorColor }} />
              {author.displayName}
            </label>
          ))}
        </div>
        <label>
          Reason
          <select value={reasonId} onChange={(event) => setReasonId(event.target.value)}>
            <option value="">Select reason</option>
            {reasons.map((reason) => <option value={reason.id} key={reason.id}>{reason.label}</option>)}
          </select>
        </label>
        <label>
          Note
          <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="Required note" />
        </label>
        <div className="modal-actions">
          <button className="primary-outline-button" onClick={onCancel}>Cancel</button>
          <button className="primary-button" onClick={onSave} disabled={!selectedAuthors.length || !note.trim() || !reasonId}>Save marks</button>
        </div>
      </div>
    </div>
  );
}

export function CalendarClearEditor({
  authors,
  selectedAuthor,
  selectedAuthors,
  setSelectedAuthors,
  selectedDates,
  onCancel,
  onClear
}: {
  authors: CalendarAuthor[];
  selectedAuthor: string;
  selectedAuthors: string[];
  setSelectedAuthors: (value: string[]) => void;
  selectedDates: string[];
  onCancel: () => void;
  onClear: () => void;
}) {
  function toggleAuthor(rawAuthor: string) {
    setSelectedAuthors(selectedAuthors.includes(rawAuthor) ? selectedAuthors.filter((item) => item !== rawAuthor) : [...selectedAuthors, rawAuthor]);
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div className="calendar-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <h2>Clear marks for {selectedDates.length} days</h2>
        <p className="calendar-helper">This removes saved marks for selected dates and selected authors.</p>
        <div className="modal-author-list">
          {authors.map((author) => (
            <label key={author.rawAuthor}>
              <input
                type="checkbox"
                checked={selectedAuthors.includes(author.rawAuthor)}
                disabled={selectedAuthor !== "all" && selectedAuthor !== author.rawAuthor}
                onChange={() => toggleAuthor(author.rawAuthor)}
              />
              <span className="color-dot" style={{ background: author.authorColor }} />
              {author.displayName}
            </label>
          ))}
        </div>
        <div className="modal-actions">
          <button className="primary-outline-button" onClick={onCancel}>Cancel</button>
          <button className="primary-button danger-solid-button" onClick={onClear} disabled={!selectedAuthors.length}>Clear marks</button>
        </div>
      </div>
    </div>
  );
}

