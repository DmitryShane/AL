import { AuthorAvatar } from "../../../AuthorAvatar";
import type { AuthorProfile } from "../../../../types/dashboard";
import { autoBreakScheduleLabel, settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";

type AutoBreakTabProps = {
  profiles: AuthorProfile[];
  drafts: Record<string, AuthorProfile>;
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  isProfileDirty: (profile: AuthorProfile) => boolean;
  onDraftChange: (rawAuthor: string, draft: AuthorProfile) => void;
  onSaveProfile: (rawAuthor: string) => void;
};

export function AutoBreakTab({
  profiles,
  drafts,
  settingsReadOnly,
  saving,
  saveStatus,
  isProfileDirty,
  onDraftChange,
  onSaveProfile
}: AutoBreakTabProps) {
  return (
    <div className="panel">
      <h2>Auto Break</h2>
      <p className="settings-caption">
        Assign authors whose first idle time during a work day should count as break time until the legal 60 minute break is filled.
      </p>
      <div className="profile-table-shell">
        <div className="auto-break-list">
          {profiles.map((profile) => {
            const draft = drafts[profile.rawAuthor] ?? profile;
            const profileDirty = isProfileDirty(profile);
            return (
              <div className="auto-break-row" key={profile.rawAuthor}>
                <div className="auto-break-identity">
                  <AuthorAvatar
                    displayName={profile.displayName || profile.rawAuthor}
                    authorColor={profile.authorColor}
                    avatarUrl={profile.avatarUrl}
                    variant="mini"
                  />
                  <div className="auto-break-identity-text">
                    <strong>{profile.displayName || profile.rawAuthor}</strong>
                    <small className="auto-break-raw-id">
                      {(draft.authorEmail ?? profile.authorEmail ?? "").trim() || profile.rawAuthor}
                    </small>
                  </div>
                </div>
                <div className="auto-break-actions">
                  <small className="auto-break-schedule">{autoBreakScheduleLabel(draft)}</small>
                  <label className="checkbox-cell">
                    <input
                      type="checkbox"
                      checked={draft.autoBreakEnabled ?? false}
                      disabled={settingsReadOnly}
                      onChange={(event) => onDraftChange(profile.rawAuthor, { ...draft, autoBreakEnabled: event.target.checked })}
                    />
                    Auto break
                  </label>
                </div>
                <button
                  className={settingsSaveButtonClassName(saveStatus[profile.rawAuthor], true)}
                  onClick={() => onSaveProfile(profile.rawAuthor)}
                  disabled={settingsReadOnly || saving === profile.rawAuthor || !profileDirty}
                >
                  {settingsSaveButtonLabel(profile.rawAuthor, saving, saveStatus)}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
