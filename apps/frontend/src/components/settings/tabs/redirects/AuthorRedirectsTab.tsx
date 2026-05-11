import type { AuthorProfile } from "../../../../types/dashboard";
import { settingsSaveButtonClassName } from "../../../../pages/pageHelpers";
import { formatBrowserDateTime } from "../deviceProfiles/formatters";

type AuthorAlias = {
  sourceRawAuthor: string;
  targetRawAuthor: string;
  createdAt?: string;
  updatedAt?: string;
  sourceDeviceId?: string;
  sourceDeviceIdHash?: string;
  sourceDeviceSource?: string;
};

type AuthorRedirectsTabProps = {
  profiles: AuthorProfile[];
  aliases: AuthorAlias[];
  aliasSource: string;
  aliasTarget: string;
  aliasError: string;
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  onAliasSourceChange: (value: string) => void;
  onAliasTargetChange: (value: string) => void;
  onSaveAuthorAlias: () => void;
  onDeleteAuthorAlias: (sourceRawAuthor: string) => void;
};

export function AuthorRedirectsTab({
  profiles,
  aliases,
  aliasSource,
  aliasTarget,
  aliasError,
  settingsReadOnly,
  saving,
  saveStatus,
  onAliasSourceChange,
  onAliasTargetChange,
  onSaveAuthorAlias,
  onDeleteAuthorAlias
}: AuthorRedirectsTabProps) {
  return (
    <div className="panel">
      <h2>Author Redirects</h2>
      <p className="settings-caption">
        Redirect a trash raw author from plugin reports to the correct author profile. The source profile is removed and future reports aggregate into the target profile.
      </p>
      <div className="profile-create-card author-alias-card">
        <label>
          Source raw author
          <input
            value={aliasSource}
            onChange={(event) => onAliasSourceChange(event.target.value)}
            list="author-alias-source-list"
            placeholder="Unknown User"
            disabled={settingsReadOnly}
          />
          <datalist id="author-alias-source-list">
            {profiles.map((profile) => (
              <option value={profile.rawAuthor} key={profile.rawAuthor} />
            ))}
          </datalist>
        </label>
        <label>
          Target profile
          <select value={aliasTarget} onChange={(event) => onAliasTargetChange(event.target.value)} disabled={settingsReadOnly}>
            {profiles.map((profile) => (
              <option value={profile.rawAuthor} key={profile.rawAuthor}>{profile.displayName || profile.rawAuthor}</option>
            ))}
          </select>
        </label>
        <button
          className={settingsSaveButtonClassName(saveStatus.authorAlias)}
          onClick={onSaveAuthorAlias}
          disabled={settingsReadOnly || saving === "authorAlias" || !aliasSource.trim() || !aliasTarget.trim()}
        >
          {saving === "authorAlias" ? "Assigning..." : saveStatus.authorAlias === "saved" ? "Assigned" : saveStatus.authorAlias === "error" ? "Failed" : "Assign"}
        </button>
      </div>
      {aliasError ? <p className="notice error">{aliasError}</p> : null}
      <div className="profile-table-shell">
        <div className="alias-list">
          {aliases.length ? (
            aliases.map((alias) => {
              const target = profiles.find((profile) => profile.rawAuthor === alias.targetRawAuthor);
              const deleteKey = `alias-delete:${alias.sourceRawAuthor}`;
              const deviceId = (alias.sourceDeviceId || alias.sourceDeviceIdHash || "").trim();
              return (
                <div className="alias-row" key={alias.sourceRawAuthor}>
                  <span>
                    <span className="alias-row-title"><strong>{alias.sourceRawAuthor}</strong> redirects to <strong>{target?.displayName || alias.targetRawAuthor}</strong></span>
                    {alias.createdAt ? <small>Created: {formatBrowserDateTime(alias.createdAt)}</small> : null}
                    {deviceId ? <small title={deviceId}>Device ID: {deviceId}</small> : null}
                  </span>
                  <button
                    className={`${settingsSaveButtonClassName(saveStatus[deleteKey], true)} danger-button`}
                    onClick={() => onDeleteAuthorAlias(alias.sourceRawAuthor)}
                    disabled={settingsReadOnly || saving === deleteKey}
                  >
                    {saving === deleteKey ? "Deleting..." : saveStatus[deleteKey] === "error" ? "Failed" : "Delete"}
                  </button>
                </div>
              );
            })
          ) : (
            <p className="empty alias-list-empty">No redirects yet.</p>
          )}
        </div>
      </div>
    </div>
  );
}
