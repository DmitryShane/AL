import type { AuthorProfile } from "../../types/dashboard";
import { profileLocalTodayIso } from "../../pages/pageHelpers";
import { Modal } from "../ui/Modal";

import "./AuthorDeleteConfirmModal.css";

export type AuthorDeleteConfirmProps = {
  profile: AuthorProfile;
  saving: boolean;
  onCancel: () => void;
  onDelete: () => void;
  description?: string;
  title?: string;
  lead?: string;
  confirmLabel?: string;
  savingLabel?: string;
  /** ISO calendar dates (YYYY-MM-DD), inclusive range shown in this modal. */
  periodStartDate?: string;
  periodEndDate?: string;
};

function formatCalendarDay(isoDate: string): string {
  const parts = isoDate.trim().split("-");

  if (parts.length !== 3) {
    return isoDate;
  }

  const year = Number(parts[0]);
  const month = Number(parts[1]);
  const day = Number(parts[2]);

  if (!year || !month || !day) {
    return isoDate;
  }

  const local = new Date(year, month - 1, day);

  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(local);
}

export function AuthorDeleteConfirm({
  profile,
  saving,
  onCancel,
  onDelete,
  description,
  title,
  lead,
  confirmLabel,
  savingLabel,
  periodStartDate,
  periodEndDate,
}: AuthorDeleteConfirmProps) {
  const defaultDescription =
    "This will remove reports, raw activity events, Telegram day/break data, report security audit records, and activity statistics for this author. The author profile, display name, Telegram username, color, and plugin settings will stay unchanged. This action cannot be undone.";

  const bodyText = description ?? defaultDescription;

  const start = periodStartDate?.trim() ?? "";
  const end = periodEndDate?.trim() ?? "";
  const hasPeriod = Boolean(start && end);
  const singleCalendarDay = hasPeriod && start === end;
  const todayIsoForAuthor = profileLocalTodayIso(profile);
  const todayOnlyAccent = singleCalendarDay && start === todayIsoForAuthor;

  return (
    <Modal
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--scoped-activity-delete"
      ariaLabelledBy="scoped-activity-delete-title"
      ariaDescribedBy={
        hasPeriod ? "scoped-activity-delete-period scoped-activity-delete-desc" : "scoped-activity-delete-desc"
      }
    >
      <div className="scoped-delete-modal__accent" aria-hidden="true" />
      <div className="scoped-delete-modal__body">
        <header className="scoped-delete-modal__header">
          <span
            className={
              todayOnlyAccent && hasPeriod
                ? "scoped-delete-modal__period-badge scoped-delete-modal__period-badge--today"
                : "scoped-delete-modal__badge"
            }
          >
            {todayOnlyAccent && hasPeriod ? "Today only" : "Selected period"}
          </span>
          <h2 id="scoped-activity-delete-title">{title ?? "Delete activity data"}</h2>
          <p className="scoped-delete-modal__lead">{lead ?? "Review the scope below, then confirm. Your choice applies only to the dates described."}</p>
        </header>

        <div className="scoped-delete-modal__profile-card">
          <span className="scoped-delete-modal__profile-label">Author</span>
          <strong className="scoped-delete-modal__profile-name">{profile.displayName}</strong>
          <span className="scoped-delete-modal__profile-email">
            {profile.authorEmail?.trim() ? profile.authorEmail.trim() : "—"}
          </span>
        </div>

        {hasPeriod ? (
          <div
            id="scoped-activity-delete-period"
            className={
              todayOnlyAccent
                ? "scoped-delete-modal__period-highlight scoped-delete-modal__period-highlight--today"
                : "scoped-delete-modal__period-highlight"
            }
          >
            {!todayOnlyAccent ? (
              <div className="scoped-delete-modal__period-highlight-head">
                {singleCalendarDay ? (
                  <span className="scoped-delete-modal__period-badge scoped-delete-modal__period-badge--single">Single calendar day</span>
                ) : (
                  <span className="scoped-delete-modal__period-badge scoped-delete-modal__period-badge--range">Inclusive date range</span>
                )}
              </div>
            ) : null}

            {singleCalendarDay ? (
              <div className="scoped-delete-modal__period-dates-main">
                <span className="scoped-delete-modal__period-iso">{start}</span>
                <span className="scoped-delete-modal__period-human">{formatCalendarDay(start)}</span>
              </div>
            ) : (
              <div className="scoped-delete-modal__period-dates-range">
                <div className="scoped-delete-modal__period-range-column">
                  <span className="scoped-delete-modal__period-range-label">Start</span>
                  <span className="scoped-delete-modal__period-iso">{start}</span>
                  <span className="scoped-delete-modal__period-human">{formatCalendarDay(start)}</span>
                </div>
                <span className="scoped-delete-modal__period-range-arrow" aria-hidden="true">
                  →
                </span>
                <div className="scoped-delete-modal__period-range-column">
                  <span className="scoped-delete-modal__period-range-label">End</span>
                  <span className="scoped-delete-modal__period-iso">{end}</span>
                  <span className="scoped-delete-modal__period-human">{formatCalendarDay(end)}</span>
                </div>
              </div>
            )}

            {todayOnlyAccent ? (
              <p className="scoped-delete-modal__period-note">
                Only this one calendar day is removed — dated using the timezone saved on this author&apos;s profile.
              </p>
            ) : null}
          </div>
        ) : null}

        <p id="scoped-activity-delete-desc" className="scoped-delete-modal__description">
          {bodyText}
        </p>

        <div className="modal-actions scoped-delete-modal__actions">
          <button className="primary-outline-button" type="button" onClick={onCancel} disabled={saving}>Cancel</button>
          <button className="primary-button danger-solid-button" type="button" onClick={onDelete} disabled={saving}>
            {saving ? savingLabel ?? "Deleting..." : confirmLabel ?? "Delete all author data"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
