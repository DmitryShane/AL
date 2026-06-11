import { useEffect, useState } from "react";

import { bulkActivityDeleteUtcRange, type BulkActivityDeletePreset } from "../../pages/pageHelpers";
import { Modal } from "../ui/Modal";

import "./AuthorDeleteAllActivityModal.css";

export type BulkAllAuthorsActivityDeleteModalProps = {
  preset: BulkActivityDeletePreset;
  authorCount: number;
  saving: boolean;
  onCancel: () => void;
  onDelete: (confirmPhrase: string) => void;
};

export function BulkAllAuthorsActivityDeleteModal({
  preset,
  authorCount,
  saving,
  onCancel,
  onDelete,
}: BulkAllAuthorsActivityDeleteModalProps) {
  const [phrase, setPhrase] = useState("");
  const requiredPhrase = preset === "full" ? "DELETE ALL ACTIVITY" : "delete";
  const phraseMatches = phrase.trim() === requiredPhrase;
  const range = bulkActivityDeleteUtcRange(preset);

  useEffect(() => {
    setPhrase("");
  }, [preset]);

  const isFull = preset === "full";

  return (
    <Modal
      data-doc-target="settings-bulk-delete-modal"
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--danger-delete-all"
      ariaLabelledBy="bulk-authors-delete-title"
    >
      <div className="danger-delete-modal__accent" aria-hidden="true" />
      <div className="danger-delete-modal__body">
        <header className="danger-delete-modal__header">
          <span className="danger-delete-modal__badge">Irreversible action</span>
          <h2 id="bulk-authors-delete-title">{isFull ? "Delete all activity for every author" : "Delete activity for every author (date range)"}</h2>
          <p className="danger-delete-modal__lead">
            {isFull ? (
              <>
                Removes <strong>all historical activity data</strong> for <strong>{authorCount}</strong> known author(s): plugins, Telegram, Discord,
                calendar marks, and rebuilt aggregates. <strong>Author profiles, site users, and settings are kept.</strong> This is not a MongoDB drop — only
                activity-shaped collections are cleared per author.
              </>
            ) : (
              <>
                Deletes activity data for <strong>all {authorCount} known author(s)</strong> whose reports fall in the inclusive UTC range{" "}
                <strong>
                  {range?.start} … {range?.end}
                </strong>
                , using the same rules as per-author ranged delete. Profiles stay unchanged.
              </>
            )}
          </p>
        </header>

        <ul className="danger-delete-modal__list">
          <li>Every author returned by the server author list (resolved aliases, same as the table below)</li>
          <li>{isFull ? "Complete activity history for each of those authors" : "Only data inside the UTC window above"}</li>
          <li>Site login accounts, profile rows, interval settings, and Discord/Telegram bot config are not removed</li>
        </ul>

        <p className="danger-delete-modal__warning">
          <strong>This cannot be undone.</strong> Export anything you need before continuing.
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
            onClick={() => onDelete(phrase.trim())}
            disabled={saving || !phraseMatches}
          >
            {saving ? "Deleting..." : isFull ? "Delete all activity for everyone" : "Delete for all authors"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
