import { useEffect, useState } from "react";

import type { AuthorProfile } from "../../types/dashboard";
import { Modal } from "../ui/Modal";

import "./AuthorDeleteAllActivityModal.css";

export type AuthorDeleteAllActivityModalProps = {
  profile: AuthorProfile;
  saving: boolean;
  onCancel: () => void;
  onDelete: () => void;
};

export function AuthorDeleteAllActivityModal({
  profile,
  saving,
  onCancel,
  onDelete,
}: AuthorDeleteAllActivityModalProps) {
  const [phrase, setPhrase] = useState("");
  const requiredPhrase = "delete";
  const phraseMatches = phrase.trim() === requiredPhrase;

  useEffect(() => {
    setPhrase("");
  }, [profile.rawAuthor]);

  return (
    <Modal
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--danger-delete-all"
      ariaLabelledBy="danger-delete-all-title"
    >
      <div className="danger-delete-modal__accent" aria-hidden="true" />
      <div className="danger-delete-modal__body">
        <header className="danger-delete-modal__header">
          <span className="danger-delete-modal__badge">Irreversible action</span>
          <h2 id="danger-delete-all-title">Delete all activity data</h2>
          <p className="danger-delete-modal__lead">
            All historical logs for this author will be removed from Activity Logger — every calendar day, all sources (plugins, Telegram, Discord, marks).
            Nothing stays except the profile row you edit in Settings.
          </p>
        </header>

        <div className="danger-delete-modal__profile-card">
          <span className="danger-delete-modal__profile-label">Author</span>
          <strong className="danger-delete-modal__profile-name">{profile.displayName}</strong>
          <span className="danger-delete-modal__profile-email">
            {profile.authorEmail?.trim() ? profile.authorEmail.trim() : "—"}
          </span>
        </div>

        <ul className="danger-delete-modal__list">
          <li>Reports, raw events, snapshots, and rebuilt aggregates</li>
          <li>Telegram days, breaks, reminders, and related prompts</li>
          <li>Discord meetings, intervals, and summaries linked to this author</li>
          <li>Calendar marks and status events tied to activity</li>
        </ul>

        <p className="danger-delete-modal__warning">
          <strong>This cannot be undone.</strong> Consider exporting anything you need before continuing.
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

        <div className="modal-actions danger-delete-modal__actions">
          <button className="primary-outline-button" type="button" onClick={onCancel} disabled={saving}>Cancel</button>
          <button
            className="primary-button danger-solid-button"
            type="button"
            onClick={onDelete}
            disabled={saving || !phraseMatches}
          >
            {saving ? "Deleting..." : "Delete all activity data"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
