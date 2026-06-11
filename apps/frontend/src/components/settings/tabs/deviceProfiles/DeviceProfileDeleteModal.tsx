import { useEffect, useState } from "react";

import { Modal } from "../../../ui/Modal";
import type { DeviceProfile } from "./types";

import "../../AuthorDeleteAllActivityModal.css";

type DeviceProfileDeleteModalProps = {
  profile: DeviceProfile;
  saving: boolean;
  deleteError?: boolean;
  onCancel: () => void;
  onDelete: () => void;
};

export function DeviceProfileDeleteModal({
  profile,
  saving,
  deleteError = false,
  onCancel,
  onDelete,
}: DeviceProfileDeleteModalProps) {
  const [phrase, setPhrase] = useState("");
  const requiredPhrase = "delete";
  const phraseMatches = phrase.trim() === requiredPhrase;

  useEffect(() => {
    setPhrase("");
  }, [profile.rawDevice]);

  return (
    <Modal
      data-doc-target="settings-device-delete-modal"
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--danger-delete-profile"
      ariaLabelledBy="danger-delete-device-profile-title"
    >
      <div className="danger-delete-modal__accent" aria-hidden="true" />
      <div className="danger-delete-modal__body">
        <header className="danger-delete-modal__header">
          <span className="danger-delete-modal__badge">Irreversible action</span>
          <h2 id="danger-delete-device-profile-title">Delete device profile</h2>
          <p className="danger-delete-modal__lead">
            This removes the stored device identity and its linked-author alias. Existing raw reports and activity data are not deleted.
          </p>
        </header>

        <div className="danger-delete-modal__profile-card">
          <span className="danger-delete-modal__profile-label">Device</span>
          <strong className="danger-delete-modal__profile-name">{profile.rawDevice}</strong>
          <span className="danger-delete-modal__profile-email">{profile.runtime || "Unknown runtime"}</span>
          <span className="danger-delete-modal__profile-raw-id" title="Linked author">
            Linked author: {profile.linkedAuthorDisplayName || profile.linkedAuthor || "Unassigned"}
          </span>
        </div>

        <ul className="danger-delete-modal__list">
          <li>Device profile identity row</li>
          <li>Linked-author alias for this raw device, if one exists</li>
          <li>Duplicate author-profile row for this raw device, if one exists</li>
        </ul>

        <p className="danger-delete-modal__warning">
          <strong>This cannot be undone.</strong> A future report from the same device can create a new profile again.
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
            Could not delete this device profile. Try again or check server logs.
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
            {saving ? "Deleting..." : "Delete device profile"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
