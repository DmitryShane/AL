import { useEffect, useState } from "react";

import type { SiteUser } from "../../types/dashboard";
import { formatSiteRole } from "../../pages/pageHelpers";
import { Modal } from "../ui/Modal";

import "./AuthorDeleteAllActivityModal.css";

export type SiteUserDeleteModalProps = {
  user: SiteUser;
  saving: boolean;
  deleteError?: boolean;
  onCancel: () => void;
  onDelete: () => void;
};

export function SiteUserDeleteModal({
  user,
  saving,
  deleteError = false,
  onCancel,
  onDelete,
}: SiteUserDeleteModalProps) {
  const [phrase, setPhrase] = useState("");
  const requiredPhrase = "delete";
  const phraseMatches = phrase.trim() === requiredPhrase;

  useEffect(() => {
    setPhrase("");
  }, [user.email]);

  const displayLabel = user.displayName?.trim() || user.email;

  return (
    <Modal
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--danger-delete-profile"
      ariaLabelledBy="danger-delete-site-user-title"
    >
      <div className="danger-delete-modal__accent" aria-hidden="true" />
      <div className="danger-delete-modal__body">
        <header className="danger-delete-modal__header">
          <span className="danger-delete-modal__badge">Irreversible action</span>
          <h2 id="danger-delete-site-user-title">Delete site user</h2>
          <p className="danger-delete-modal__lead">
            This removes the dashboard login for this email. Activity Logger author profiles, plugins, Telegram data, and stored reports are not changed — only site access for this account is removed.
          </p>
        </header>

        <div className="danger-delete-modal__profile-card">
          <span className="danger-delete-modal__profile-label">User</span>
          <strong className="danger-delete-modal__profile-name">{displayLabel}</strong>
          <span className="danger-delete-modal__profile-email">{user.email}</span>
          <span className="danger-delete-modal__profile-raw-id" title="Role on the dashboard">
            Role: {formatSiteRole(user.role)}
          </span>
        </div>

        <ul className="danger-delete-modal__list">
          <li>Site login row for this email (cannot sign in until recreated)</li>
          <li>Stored password hash and forced password resets tied to this row</li>
          <li>Role assignment (Admin / Editor / Viewer) for this login only</li>
        </ul>

        <p className="danger-delete-modal__warning">
          <strong>This cannot be undone.</strong> Make sure another admin account stays active before deleting your own access.
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
            Could not delete this user. Try again or check server logs.
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
            {saving ? "Deleting..." : "Delete site user"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
