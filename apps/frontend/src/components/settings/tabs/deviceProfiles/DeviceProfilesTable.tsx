import { formatDeviceDateTime, formatDeviceTracking } from "./formatters";
import type { DeviceProfile, DeviceProfileAuthorOption } from "./types";

type DeviceProfilesTableProps = {
  deviceProfiles: DeviceProfile[];
  authorOptions: DeviceProfileAuthorOption[];
  loading: boolean;
  savingRawDevice: string;
  deletingRawDevice: string;
  aliasDrafts: Record<string, string>;
  onLinkedAuthorDraftChange: (rawDevice: string, targetRawAuthor: string) => void;
  onSaveLinkedAuthor: (rawDevice: string) => void;
  onDeleteProfile: (profile: DeviceProfile) => void;
};

export function DeviceProfilesTable({
  deviceProfiles,
  authorOptions,
  loading,
  savingRawDevice,
  deletingRawDevice,
  aliasDrafts,
  onLinkedAuthorDraftChange,
  onSaveLinkedAuthor,
  onDeleteProfile,
}: DeviceProfilesTableProps) {
  return (
    <div className="profile-table-shell">
      <div className="profile-table profile-table--device-profiles">
        <div className="profile-table-head">
          <span>Raw Device</span>
          <span>Linked Author</span>
          <span>Runtime</span>
          <span>IDFA</span>
          <span>GAID</span>
          <span>Project</span>
          <span>Plugin</span>
          <span>ATT / GAID</span>
          <span>Created</span>
          <span>Last Seen</span>
          <span>Actions</span>
        </div>
        {loading ? (
          <p className="profile-table-empty">Loading device profiles...</p>
        ) : deviceProfiles.length === 0 ? (
          <p className="profile-table-empty">No device profiles found.</p>
        ) : (
          deviceProfiles.map((profile) => {
            const draftValue = aliasDrafts[profile.rawDevice] ?? profile.linkedAuthor ?? "";
            const changed = draftValue !== (profile.linkedAuthor ?? "");
            const saving = savingRawDevice === profile.rawDevice;
            const deleting = deletingRawDevice === profile.rawDevice;

            return (
              <div className="profile-row" key={`${profile.source ?? ""}:${profile.rawDevice}`}>
                <span className="profile-author-cell profile-device-raw-cell" title={profile.rawDevice}>
                  <strong>{profile.rawDevice || "-"}</strong>
                </span>
                <div className="device-profile-author-cell">
                  <select
                    value={draftValue}
                    disabled={saving || deleting}
                    onChange={(event) => onLinkedAuthorDraftChange(profile.rawDevice, event.target.value)}
                  >
                    <option value="" disabled>Unassigned</option>
                    {authorOptions.map((author) => (
                      <option key={author.rawAuthor} value={author.rawAuthor}>
                        {author.displayName}
                      </option>
                    ))}
                  </select>
                </div>
                <span className="profile-readonly-cell">{profile.runtime || "-"}</span>
                <span className="profile-readonly-cell" title={profile.idfa || ""}>{profile.idfa || "-"}</span>
                <span className="profile-readonly-cell" title={profile.gaid || ""}>{profile.gaid || "-"}</span>
                <span className="profile-readonly-cell" title={profile.projectId || ""}>{profile.projectId || "-"}</span>
                <span className="profile-readonly-cell">{profile.pluginVersion || "-"}</span>
                <span className="profile-readonly-cell" title={profile.trackingAuthorizationStatus || ""}>
                  {formatDeviceTracking(profile)}
                </span>
                <span className="profile-readonly-cell" title={profile.createdAt || ""}>{formatDeviceDateTime(profile.createdAt)}</span>
                <span className="profile-readonly-cell" title={profile.lastSeenAt || ""}>
                  {formatDeviceDateTime(profile.lastSeenAt)}
                </span>
                <div className="profile-actions">
                  <button
                    className="primary-outline-button"
                    type="button"
                    disabled={!changed || !draftValue || saving || deleting}
                    onClick={() => onSaveLinkedAuthor(profile.rawDevice)}
                  >
                    {saving ? "Saving..." : "Save"}
                  </button>
                  <button
                    className="primary-button danger-solid-button delete-all-data-solid-button"
                    type="button"
                    disabled={saving || deleting}
                    onClick={() => onDeleteProfile(profile)}
                  >
                    {deleting ? "Deleting..." : "Delete"}
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
