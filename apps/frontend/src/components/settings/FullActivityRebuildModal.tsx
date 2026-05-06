import { useState } from "react";

import { Modal } from "../ui/Modal";

import "./AuthorDeleteAllActivityModal.css";

export type FullActivityRebuildModalProps = {
  saving: boolean;
  onCancel: () => void;
  onRebuild: (confirmPhrase: string) => void;
};

export function FullActivityRebuildModal({
  saving,
  onCancel,
  onRebuild,
}: FullActivityRebuildModalProps) {
  const [phrase, setPhrase] = useState("");
  const requiredPhrase = "REBUILD ALL ACTIVITY";
  const phraseMatches = phrase.trim() === requiredPhrase;

  return (
    <Modal
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--danger-delete-all"
      ariaLabelledBy="full-activity-rebuild-title"
    >
      <div className="danger-delete-modal__accent" aria-hidden="true" />
      <div className="danger-delete-modal__body">
        <header className="danger-delete-modal__header">
          <span className="danger-delete-modal__badge">Heavy maintenance</span>
          <h2 id="full-activity-rebuild-title">Rebuild all activity aggregates</h2>
          <p className="danger-delete-modal__lead">
            This rebuilds report rows, daily activity, and aggregate state for the complete database from the raw activity sources. It does not delete author
            profiles, users, or settings, but it can take a while.
          </p>
        </header>

        <ul className="danger-delete-modal__list">
          <li>Every known author and every date with raw activity data</li>
          <li>Derived activity rows and daily aggregates are recalculated</li>
          <li>The dashboard cache is refreshed after the rebuild finishes</li>
        </ul>

        <p className="danger-delete-modal__warning">
          Use this only when the whole local database needs recalculation.
        </p>

        <label className="danger-delete-modal__phrase-field">
          <span className="danger-delete-modal__phrase-label">
            Type <span className="danger-delete-modal__phrase-keyword">{requiredPhrase}</span> to confirm
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
          <button className="primary-outline-button" type="button" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button
            className="primary-button danger-solid-button"
            type="button"
            onClick={() => onRebuild(phrase.trim())}
            disabled={saving || !phraseMatches}
          >
            {saving ? "Rebuilding..." : "Rebuild all activity"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
