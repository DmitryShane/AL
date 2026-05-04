import { useEffect, useState } from "react";

import type { AuthorProfile } from "../../types/dashboard";
import { Modal } from "../ui/Modal";

import "./AuthorDeleteAllActivityModal.css";

export type AuthorProfileDeleteModalProps = {
  profile: AuthorProfile;
  saving: boolean;
  deleteError?: boolean;
  onCancel: () => void;
  onDelete: () => void;
};

export function AuthorProfileDeleteModal({
  profile,
  saving,
  deleteError = false,
  onCancel,
  onDelete,
}: AuthorProfileDeleteModalProps) {
  const [phrase, setPhrase] = useState("");
  const requiredPhrase = "delete";
  const phraseMatches = phrase.trim() === requiredPhrase;

  useEffect(() => {
    setPhrase("");
  }, [profile.rawAuthor]);

  const displayLabel = profile.displayName?.trim() || profile.rawAuthor;

  return (
    <Modal
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--danger-delete-profile"
      ariaLabelledBy="danger-delete-profile-title"
    >
      <div className="danger-delete-modal__accent" aria-hidden="true" />
      <div className="danger-delete-modal__body">
        <header className="danger-delete-modal__header">
          <span className="danger-delete-modal__badge">Irreversible action</span>
          <h2 id="danger-delete-profile-title">Delete author profile</h2>
          <p className="danger-delete-modal__lead">
            This removes the Settings profile row and permanently deletes every mapping and activity record tied to this author.
            Unlike <strong>Delete all data</strong>, nothing remains for this raw author — you would need to add the profile again.
          </p>
        </header>

        <div className="danger-delete-modal__profile-card">
          <span className="danger-delete-modal__profile-label">Author</span>
          <strong className="danger-delete-modal__profile-name">{displayLabel}</strong>
          <span className="danger-delete-modal__profile-email">
            {profile.authorEmail?.trim() ? profile.authorEmail.trim() : "—"}
          </span>
          <span className="danger-delete-modal__profile-raw-id" title="Raw author id from plugins">
            {profile.rawAuthor}
          </span>
        </div>

        <ul className="danger-delete-modal__list">
          <li>Author profile, Telegram username mapping, Discord IDs and names</li>
          <li>Plugin enable flag, color, timezone preferences stored on the profile</li>
          <li>Reports, raw events, snapshots, report security audit records, and rebuilt aggregates</li>
          <li>Telegram work-day rows, breaks, reminders, and related prompts</li>
          <li>Discord meetings, intervals, and summaries linked to this author</li>
          <li>Calendar marks and dashboard statistics for this author</li>
        </ul>

        <p className="danger-delete-modal__warning">
          <strong>This cannot be undone.</strong> Export anything you need before continuing.
        </p>

        <label className="danger-delete-modal__phrase-field">
          <span className="danger-delete-modal__phrase-label">
            Type <span className="danger-delete-modal__phrase-keyword">{requiredPhrase}</span> to confirm you understand
          </span>
          <input
            type="text"
            value={phrase}
            onChange={(event) => setPhrase(event.target.value)}
            disabled={saving}
            autoComplete="off"
            spellCheck={false}
            placeholder={requiredPhrase}
          />
        </label>

        {deleteError ? (
          <p className="danger-delete-modal__delete-error" role="alert">
            Could not delete this profile. Try again or check server logs.
          </p>
        ) : null}

        <div className="modal-actions danger-delete-modal__actions">
          <button className="primary-outline-button" type="button" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button
            className="primary-button danger-solid-button"
            type="button"
            onClick={onDelete}
            disabled={saving || !phraseMatches}
          >
            {saving ? "Deleting..." : "Delete profile and all data"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
