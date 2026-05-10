import { useEffect, useState } from "react";

import { Modal } from "../../../ui/Modal";

import "../../AuthorDeleteAllActivityModal.css";

type DeviceProfilesBulkDeleteModalProps = {
  deviceCount: number;
  saving: boolean;
  deleteError?: boolean;
  onCancel: () => void;
  onDelete: () => void;
};

export function DeviceProfilesBulkDeleteModal({
  deviceCount,
  saving,
  deleteError = false,
  onCancel,
  onDelete,
}: DeviceProfilesBulkDeleteModalProps) {
  const [phrase, setPhrase] = useState("");
  const requiredPhrase = "delete";
  const phraseMatches = phrase.trim() === requiredPhrase;

  useEffect(() => {
    setPhrase("");
  }, [deviceCount]);

  return (
    <Modal
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--danger-delete-all"
      ariaLabelledBy="danger-delete-device-profiles-title"
    >
      <div className="danger-delete-modal__accent" aria-hidden="true" />
      <div className="danger-delete-modal__body">
        <header className="danger-delete-modal__header">
          <span className="danger-delete-modal__badge">Irreversible action</span>
          <h2 id="danger-delete-device-profiles-title">Delete all device profiles</h2>
          <p className="danger-delete-modal__lead">
            This removes <strong>{deviceCount}</strong> stored device profile(s) and their author links. Existing raw reports and activity data are not deleted.
          </p>
        </header>

        <ul className="danger-delete-modal__list">
          <li>Every device profile identity row currently shown in Device Profiles</li>
          <li>Author links for those raw devices, if they exist</li>
          <li>Duplicate author-profile rows for those raw devices, if they exist</li>
        </ul>

        <p className="danger-delete-modal__warning">
          <strong>This cannot be undone.</strong> Future reports from these devices can create new profiles again.
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
            Could not delete device profiles. Try again or check server logs.
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
            {saving ? "Deleting..." : "Delete all device profiles"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
