import type { Dispatch, SetStateAction } from "react";
import { AuthorAvatar } from "../../../AuthorAvatar";
import type { AuthorProfile } from "../../../../types/dashboard";
import {
  formatProfileTimeZoneLabel,
  formatProfileTimeZoneTitle,
  profileLocalTodayIso,
  settingsSaveButtonClassName,
  settingsSaveButtonLabel,
  type BulkActivityDeletePreset
} from "../../../../pages/pageHelpers";
import { AuthorDeleteAllActivityModal } from "../../AuthorDeleteAllActivityModal";
import { BulkAllAuthorsActivityDeleteModal } from "../../BulkAllAuthorsActivityDeleteModal";
import { FullActivityRebuildModal } from "../../FullActivityRebuildModal";
import { AuthorDeleteConfirm, AuthorProfileDeleteModal } from "../../SettingsComponents";

type DeleteActivityDraft = {
  mode: "today" | "range";
  rangeStart: string;
  rangeEnd: string;
};

type PendingAuthorActivityDelete =
  | { mode: "range"; profile: AuthorProfile; startDate: string; endDate: string }
  | { mode: "all"; profile: AuthorProfile };

type PendingAuthorActivityRebuild = {
  profile: AuthorProfile;
  startDate: string;
  endDate: string;
};

type ActivityRebuildProgress = {
  jobId: string;
  label: string;
  status: "running" | "completed" | "failed";
  phase: string;
  progress: number;
  error?: string;
};

type AuthorProfilesTabProps = {
  profiles: AuthorProfile[];
  drafts: Record<string, AuthorProfile>;
  newProfile: AuthorProfile;
  avatarRefreshCadence: "week" | "month";
  deleteActivityDrafts: Record<string, DeleteActivityDraft>;
  pendingAuthorActivityDelete: PendingAuthorActivityDelete | null;
  pendingAuthorActivityRebuild: PendingAuthorActivityRebuild | null;
  deleteActivityFieldError: Record<string, string>;
  deleteProfileTarget: AuthorProfile | null;
  bulkActivityDeletePreset: BulkActivityDeletePreset;
  bulkActivityDeleteModalOpen: boolean;
  fullActivityRebuildModalOpen: boolean;
  activityRebuildProgress: ActivityRebuildProgress | null;
  canManageSettings: boolean;
  settingsReadOnly: boolean;
  avatarSettingsLockedTitle: string;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  isAvatarCadenceDirty: boolean;
  isProfileDirty: (profile: AuthorProfile) => boolean;
  onDraftChange: (rawAuthor: string, draft: AuthorProfile) => void;
  onNewProfileChange: Dispatch<SetStateAction<AuthorProfile>>;
  onAvatarRefreshCadenceChange: (value: "week" | "month") => void;
  onDeleteActivityDraftChange: (rawAuthor: string, draft: DeleteActivityDraft) => void;
  onBulkActivityDeletePresetChange: (value: BulkActivityDeletePreset) => void;
  onBulkActivityDeleteModalOpenChange: (value: boolean) => void;
  onFullActivityRebuildModalOpenChange: (value: boolean) => void;
  onPendingAuthorActivityDeleteChange: (value: PendingAuthorActivityDelete | null) => void;
  onPendingAuthorActivityRebuildChange: (value: PendingAuthorActivityRebuild | null) => void;
  onDeleteProfileTargetChange: (value: AuthorProfile | null) => void;
  onCreateProfile: () => void;
  onSaveProfile: (rawAuthor: string) => void;
  onSaveAvatarRefreshCadence: () => void;
  onRefreshAllGitHubAvatars: () => void;
  onRefreshAuthorGitHubAvatar: (rawAuthor: string) => void;
  onRequestAuthorActivityDelete: (profile: AuthorProfile) => void;
  onRequestAuthorActivityRebuild: (profile: AuthorProfile) => void;
  onRequestAuthorDeleteAllActivity: (profile: AuthorProfile) => void;
  onExecuteBulkActivityDeleteAllAuthors: (confirmPhrase: string) => void;
  onExecuteFullActivityRebuild: (confirmPhrase: string) => void;
  onExecuteAuthorActivityDelete: (pending: PendingAuthorActivityDelete) => void;
  onExecuteAuthorActivityRebuild: (pending: PendingAuthorActivityRebuild) => void;
  onDeleteAuthorProfile: (rawAuthor: string) => void;
};

export function AuthorProfilesTab({
  profiles,
  drafts,
  newProfile,
  avatarRefreshCadence,
  deleteActivityDrafts,
  pendingAuthorActivityDelete,
  pendingAuthorActivityRebuild,
  deleteActivityFieldError,
  deleteProfileTarget,
  bulkActivityDeletePreset,
  bulkActivityDeleteModalOpen,
  fullActivityRebuildModalOpen,
  activityRebuildProgress,
  canManageSettings,
  settingsReadOnly,
  avatarSettingsLockedTitle,
  saving,
  saveStatus,
  isAvatarCadenceDirty,
  isProfileDirty,
  onDraftChange,
  onNewProfileChange,
  onAvatarRefreshCadenceChange,
  onDeleteActivityDraftChange,
  onBulkActivityDeletePresetChange,
  onBulkActivityDeleteModalOpenChange,
  onFullActivityRebuildModalOpenChange,
  onPendingAuthorActivityDeleteChange,
  onPendingAuthorActivityRebuildChange,
  onDeleteProfileTargetChange,
  onCreateProfile,
  onSaveProfile,
  onSaveAvatarRefreshCadence,
  onRefreshAllGitHubAvatars,
  onRefreshAuthorGitHubAvatar,
  onRequestAuthorActivityDelete,
  onRequestAuthorActivityRebuild,
  onRequestAuthorDeleteAllActivity,
  onExecuteBulkActivityDeleteAllAuthors,
  onExecuteFullActivityRebuild,
  onExecuteAuthorActivityDelete,
  onExecuteAuthorActivityRebuild,
  onDeleteAuthorProfile
}: AuthorProfilesTabProps) {
  const rebuildRunning = activityRebuildProgress?.status === "running";

  return (
<>
      <div className="panel">
<h2>Author Profiles</h2>
<p className="settings-caption">
  Telegram and Discord mappings link chat and meeting events to the author.
  Raw Author must exactly match the value sent by activity logger plugins.
</p>
<div className="profile-create-card">
  <label>
    Raw Author
    <input
      value={newProfile.rawAuthor}
      onChange={(event) => onNewProfileChange((profile) => ({ ...profile, rawAuthor: event.target.value }))}
      placeholder="Git user.name"
      autoComplete="off"
      data-1p-ignore
      data-lpignore="true"
      disabled={settingsReadOnly}
    />
  </label>
  <label>
    Display Name
    <input
      value={newProfile.displayName}
      onChange={(event) => onNewProfileChange((profile) => ({ ...profile, displayName: event.target.value }))}
      placeholder="Shown on dashboard"
      autoComplete="off"
      data-1p-ignore
      data-lpignore="true"
      disabled={settingsReadOnly}
    />
  </label>
  <label>
    Team
    <input
      value={newProfile.team ?? ""}
      onChange={(event) => onNewProfileChange((profile) => ({ ...profile, team: event.target.value }))}
      placeholder="Team"
      autoComplete="off"
      data-1p-ignore
      data-lpignore="true"
      disabled={settingsReadOnly}
    />
  </label>
  <label>
    GitHub
    <input
      value={newProfile.githubUsername ?? ""}
      onChange={(event) => onNewProfileChange((profile) => ({ ...profile, githubUsername: event.target.value }))}
      placeholder="username"
      autoComplete="off"
      data-1p-ignore
      data-lpignore="true"
      disabled={settingsReadOnly}
    />
  </label>
  <label>
    Telegram
    <input
      value={newProfile.telegramUsername ?? ""}
      onChange={(event) => onNewProfileChange((profile) => ({ ...profile, telegramUsername: event.target.value }))}
      placeholder="@username"
      autoComplete="off"
      data-1p-ignore
      data-lpignore="true"
      disabled={settingsReadOnly}
    />
  </label>
  <label>
    Discord ID
    <input
      value={newProfile.discordUserId ?? ""}
      onChange={(event) => onNewProfileChange((profile) => ({ ...profile, discordUserId: event.target.value }))}
      placeholder="User ID"
      autoComplete="off"
      data-1p-ignore
      data-lpignore="true"
      disabled={settingsReadOnly}
    />
  </label>
  <label>
    Discord Name
    <input
      value={newProfile.discordUsername ?? ""}
      onChange={(event) => onNewProfileChange((profile) => ({ ...profile, discordUsername: event.target.value }))}
      placeholder="username"
      autoComplete="off"
      data-1p-ignore
      data-lpignore="true"
      disabled={settingsReadOnly}
    />
  </label>
  <label>
    Color
    <input
      type="color"
      value={newProfile.authorColor ?? "#13a37b"}
      onChange={(event) => onNewProfileChange((profile) => ({ ...profile, authorColor: event.target.value }))}
      disabled={settingsReadOnly}
    />
  </label>
  <label>
    Plugin
    <span className="checkbox-cell">
      <input
        type="checkbox"
        checked={newProfile.pluginEnabled ?? true}
        onChange={(event) => onNewProfileChange((profile) => ({ ...profile, pluginEnabled: event.target.checked }))}
        disabled={settingsReadOnly}
      />
      Enabled
    </span>
  </label>
  <button
    className={settingsSaveButtonClassName(saveStatus.newProfile)}
    onClick={() => onCreateProfile()}
    disabled={settingsReadOnly || saving === "newProfile" || !newProfile.rawAuthor.trim()}
  >
    {saving === "newProfile" ? "Creating..." : saveStatus.newProfile === "saved" ? "Created" : saveStatus.newProfile === "error" ? "Failed" : "Add profile"}
  </button>
</div>
<div className="profile-table-shell">
  <div className="profile-table">
    <div className="profile-table-head">
      <span>Raw Author</span>
      <span>Display Name</span>
      <span>Team</span>
      <span>GitHub</span>
      <span>Telegram</span>
      <span>Discord ID</span>
      <span>Discord Name</span>
      <span>Timezone</span>
      <span>Color</span>
      <span>Plugin</span>
      <span>Actions</span>
    </div>
    {profiles.map((profile) => {
    const draft = drafts[profile.rawAuthor] ?? profile;
    const profileDirty = isProfileDirty(profile);
    const deleteProfileKey = `delete-profile:${profile.rawAuthor}`;
    const profileSubline = profile.authorEmail || "-";
    return (
      <div className="profile-row" key={profile.rawAuthor}>
        <span className="profile-author-cell profile-author-cell--with-avatar" title={profileSubline || profile.rawAuthor}>
          <AuthorAvatar
            displayName={(draft.displayName || profile.rawAuthor).trim() || profile.rawAuthor}
            authorColor={draft.authorColor ?? profile.authorColor}
            avatarUrl={draft.avatarUrl ?? profile.avatarUrl}
            variant="mini"
          />
          <span className="profile-author-cell-text">
            <strong>{profile.rawAuthor}</strong>
            <small>{profileSubline}</small>
          </span>
        </span>
        <input
          value={draft.displayName}
          onChange={(event) => onDraftChange(profile.rawAuthor, { ...draft, displayName: event.target.value })}
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          disabled={settingsReadOnly}
        />
        <input
          value={draft.team ?? ""}
          onChange={(event) => onDraftChange(profile.rawAuthor, { ...draft, team: event.target.value })}
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          disabled={settingsReadOnly}
        />
        <input
          value={draft.githubUsername ?? ""}
          onChange={(event) =>
            onDraftChange(profile.rawAuthor, { ...draft, githubUsername: event.target.value })
          }
          placeholder="username"
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          disabled={settingsReadOnly}
        />
        <input
          value={draft.telegramUsername ?? ""}
          onChange={(event) =>
            onDraftChange(profile.rawAuthor, { ...draft, telegramUsername: event.target.value })
          }
          placeholder="@username"
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          disabled={settingsReadOnly}
        />
        <input
          value={draft.discordUserId ?? ""}
          onChange={(event) =>
            onDraftChange(profile.rawAuthor, { ...draft, discordUserId: event.target.value })
          }
          placeholder="User ID"
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          disabled={settingsReadOnly}
        />
        <input
          value={draft.discordUsername ?? ""}
          onChange={(event) =>
            onDraftChange(profile.rawAuthor, { ...draft, discordUsername: event.target.value })
          }
          placeholder="username"
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          disabled={settingsReadOnly}
        />
        <span className="profile-readonly-cell" title={formatProfileTimeZoneTitle(profile)}>{formatProfileTimeZoneLabel(profile)}</span>
        <input
          type="color"
          value={draft.authorColor ?? "#13a37b"}
          onChange={(event) => onDraftChange(profile.rawAuthor, { ...draft, authorColor: event.target.value })}
          disabled={settingsReadOnly}
        />
        <label className="checkbox-cell">
          <input
            type="checkbox"
            checked={draft.pluginEnabled ?? true}
            disabled={settingsReadOnly}
            onChange={(event) =>
              onDraftChange(profile.rawAuthor, { ...draft, pluginEnabled: event.target.checked })
            }
          />
          Enabled
        </label>
        <div className="profile-actions">
          <button
            className={settingsSaveButtonClassName(saveStatus[profile.rawAuthor], true)}
            onClick={() => onSaveProfile(profile.rawAuthor)}
            disabled={settingsReadOnly || saving === profile.rawAuthor || !profileDirty}
          >
            {settingsSaveButtonLabel(profile.rawAuthor, saving, saveStatus)}
          </button>
          <button
            type="button"
            className="primary-button danger-solid-button delete-all-data-solid-button"
            onClick={() => {
              if (!settingsReadOnly) {
                onDeleteProfileTargetChange(profile);
              }
            }}
            disabled={settingsReadOnly || saving === deleteProfileKey}
          >
            {saving === deleteProfileKey ? "Deleting..." : "Delete profile"}
          </button>
        </div>
      </div>
    );
  })}
  </div>
</div>
      </div>
      <div className="settings-card-row settings-author-avatars-delete-row">
<div className="panel github-avatars-panel">
<h2>GitHub avatars</h2>
<p className="settings-caption">
  Profile pictures are cached from GitHub when a login is set. Use the buttons below to fetch the latest image immediately.
  Between manual refreshes, cached images renew automatically at the start of a new calendar period (UTC), depending on the setting below.
  Rows without a GitHub login are skipped.
</p>
<div className="settings-row github-avatars-toolbar">
  <label title={canManageSettings ? undefined : avatarSettingsLockedTitle}>
    Auto-refresh cadence (UTC)
    <select
      value={avatarRefreshCadence}
      onChange={(event) => onAvatarRefreshCadenceChange(event.target.value === "week" ? "week" : "month")}
      disabled={!canManageSettings}
    >
      <option value="week">Week (ISO week)</option>
      <option value="month">Month</option>
    </select>
  </label>
  <button
    type="button"
    className={settingsSaveButtonClassName(saveStatus.avatarCadence)}
    onClick={() => onSaveAvatarRefreshCadence()}
    disabled={saving === "avatarCadence" || !isAvatarCadenceDirty || !canManageSettings}
    title={canManageSettings ? undefined : avatarSettingsLockedTitle}
  >
    {saving === "avatarCadence" ? "Saving..." : saveStatus.avatarCadence === "saved" ? "Saved" : saveStatus.avatarCadence === "error" ? "Failed" : "Save"}
  </button>
  <button
    type="button"
    className={`github-avatars-refresh-all ${settingsSaveButtonClassName(saveStatus.avatarRefreshAll)}`}
    onClick={() => onRefreshAllGitHubAvatars()}
    disabled={saving === "avatar-refresh-all" || !canManageSettings}
    title={canManageSettings ? undefined : avatarSettingsLockedTitle}
  >
    {saving === "avatar-refresh-all"
      ? "Refreshing..."
      : saveStatus.avatarRefreshAll === "saved"
        ? "Done"
        : saveStatus.avatarRefreshAll === "error"
          ? "Failed"
          : "Refresh avatars for all"}
  </button>
</div>
<div className="profile-table-shell profile-table-shell--compact">
  <div className="profile-table profile-table--github-avatars">
    <div className="profile-table-head">
      <span>Raw Author</span>
      <span>GitHub</span>
      <span>Actions</span>
    </div>
    {profiles.map((profile) => {
      const draft = drafts[profile.rawAuthor] ?? profile;
      const githubLogin = (draft.githubUsername ?? "").trim();
      const hasGithub = Boolean(githubLogin);
      const refreshKey = `avatar-refresh:${profile.rawAuthor}`;
      return (
        <div className="profile-row" key={`avatar-refresh:${profile.rawAuthor}`}>
          <span className="profile-author-cell profile-author-cell--with-avatar" title={draft.authorEmail || profile.rawAuthor}>
            <AuthorAvatar
              displayName={(draft.displayName || profile.rawAuthor).trim() || profile.rawAuthor}
              authorColor={draft.authorColor ?? profile.authorColor}
              avatarUrl={draft.avatarUrl ?? profile.avatarUrl}
              variant="mini"
            />
            <span className="profile-author-cell-text">
              <strong>{profile.rawAuthor}</strong>
              <small>{draft.authorEmail || profile.authorEmail || "-"}</small>
            </span>
          </span>
          <span className="profile-readonly-cell" title={githubLogin}>
            {githubLogin || "—"}
          </span>
          <div className="profile-actions">
            <button
              type="button"
              className={settingsSaveButtonClassName(saveStatus[refreshKey], true)}
              onClick={() => onRefreshAuthorGitHubAvatar(profile.rawAuthor)}
              disabled={!hasGithub || saving === refreshKey || !canManageSettings}
              title={canManageSettings ? undefined : avatarSettingsLockedTitle}
            >
              {saving === refreshKey
                ? "Refreshing..."
                : saveStatus[refreshKey] === "saved"
                  ? "Refreshed"
                  : saveStatus[refreshKey] === "error"
                    ? "Failed"
                    : "Refresh"}
            </button>
          </div>
        </div>
      );
    })}
  </div>
</div>
      </div>
      <div className="panel delete-activity-data-panel">
<h2>Data Base</h2>
<p className="settings-caption">
  Manage the activity database for all authors: rebuild derived aggregates for a selected period, delete scoped activity data, or wipe an author&apos;s full activity history. To wipe everything for an author, use <strong>Delete all data</strong> — that opens a separate confirmation step (you must type <strong>delete</strong>). The profile row stays unchanged unless you delete the profile above.
  &quot;Today&quot; uses each author&apos;s timezone when set on the profile; otherwise your browser&apos;s local calendar date.
</p>
{activityRebuildProgress ? (
  <div className={`database-rebuild-progress database-rebuild-progress--${activityRebuildProgress.status}`}>
    <div className="database-rebuild-progress__header">
      <strong>{activityRebuildProgress.label}</strong>
      <span>{Math.round(activityRebuildProgress.progress)}%</span>
    </div>
    <div className="database-rebuild-progress__track" aria-label="Activity rebuild progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(activityRebuildProgress.progress)}>
      <span style={{ width: `${Math.max(0, Math.min(100, activityRebuildProgress.progress))}%` }} />
    </div>
    <p>{activityRebuildProgress.status === "completed" ? "Completed" : activityRebuildProgress.status === "failed" ? "Failed" : activityRebuildProgress.phase}</p>
    {activityRebuildProgress.status === "failed" && activityRebuildProgress.error ? (
      <p className="alert-text database-rebuild-progress__error">{activityRebuildProgress.error.split("\n")[0]}</p>
    ) : null}
  </div>
) : null}
<div className="settings-row github-avatars-toolbar delete-activity-bulk-toolbar">
  <label>
    Bulk delete for every author (UTC window)
    <select
      value={bulkActivityDeletePreset}
      onChange={(event) => onBulkActivityDeletePresetChange(event.target.value as BulkActivityDeletePreset)}
      disabled={settingsReadOnly}
    >
      <option value="1d">1 calendar day (today UTC)</option>
      <option value="2d">2 calendar days</option>
      <option value="3d">3 calendar days</option>
      <option value="week">7 calendar days</option>
      <option value="month">30 calendar days</option>
      <option value="full">All activity history (every author)</option>
    </select>
  </label>
  <div className="delete-activity-bulk-actions">
    <button
      type="button"
      className="primary-button danger-solid-button delete-all-data-solid-button"
      onClick={() => {
        if (!settingsReadOnly) {
          onBulkActivityDeleteModalOpenChange(true);
        }
      }}
      disabled={settingsReadOnly || saving === "bulk-delete-all-authors" || profiles.length === 0}
    >
      Delete for all authors…
    </button>
    <button
      type="button"
      className="primary-button danger-solid-button delete-all-data-solid-button"
      onClick={() => {
        if (!settingsReadOnly) {
          onFullActivityRebuildModalOpenChange(true);
        }
      }}
    disabled={settingsReadOnly || saving === "full-activity-rebuild" || rebuildRunning}
    >
      Rebuild full DB…
    </button>
  </div>
</div>
<div className="profile-table-shell profile-table-shell--compact">
  <div className="profile-table profile-table--delete-activity">
    <div className="profile-table-head">
      <span>Raw Author</span>
      <span>Period</span>
      <span>Actions</span>
    </div>
    {profiles.map((profile) => {
      const actDraft =
        deleteActivityDrafts[profile.rawAuthor] ?? { mode: "today" as const, rangeStart: "", rangeEnd: "" };
      const deleteActivityKey = `delete:${profile.rawAuthor}`;
      const draft = drafts[profile.rawAuthor] ?? profile;
      return (
        <div className="profile-row" key={`delete-activity:${profile.rawAuthor}`}>
          <span className="profile-author-cell profile-author-cell--with-avatar" title={profile.authorEmail || profile.rawAuthor}>
            <AuthorAvatar
              displayName={(draft.displayName || profile.rawAuthor).trim() || profile.rawAuthor}
              authorColor={draft.authorColor ?? profile.authorColor}
              avatarUrl={draft.avatarUrl ?? profile.avatarUrl}
              variant="mini"
            />
            <span className="profile-author-cell-text">
              <strong>{profile.rawAuthor}</strong>
              <small>{profile.authorEmail?.trim() ? profile.authorEmail.trim() : "—"}</small>
            </span>
          </span>
          <div className="profile-delete-activity-period-cell">
            <div className="delete-activity-controls">
              <div className="delete-activity-mode-row">
                <label className="radio-inline">
                  <input
                    type="radio"
                    name={`delete-mode-${profile.rawAuthor}`}
                    checked={actDraft.mode === "today"}
                    disabled={settingsReadOnly}
                    onChange={() => onDeleteActivityDraftChange(profile.rawAuthor, { ...actDraft, mode: "today" })}
                  />
                  Today
                </label>
                <label className="radio-inline">
                  <input
                    type="radio"
                    name={`delete-mode-${profile.rawAuthor}`}
                    checked={actDraft.mode === "range"}
                    disabled={settingsReadOnly}
                    onChange={() => onDeleteActivityDraftChange(profile.rawAuthor, { ...actDraft, mode: "range" })}
                  />
                  Custom range
                </label>
              </div>
              {actDraft.mode === "range" ? (
                <div className="delete-activity-date-row">
                  <label>
                    Start
                    <input
                      type="date"
                      value={actDraft.rangeStart}
                      disabled={settingsReadOnly}
                      onChange={(event) => onDeleteActivityDraftChange(profile.rawAuthor, { ...actDraft, rangeStart: event.target.value })}
                    />
                  </label>
                  <label>
                    End
                    <input
                      type="date"
                      value={actDraft.rangeEnd}
                      disabled={settingsReadOnly}
                      onChange={(event) => onDeleteActivityDraftChange(profile.rawAuthor, { ...actDraft, rangeEnd: event.target.value })}
                    />
                  </label>
                </div>
              ) : null}
            </div>
            {deleteActivityFieldError[profile.rawAuthor] ? (
              <p className="alert-text delete-activity-row-error">{deleteActivityFieldError[profile.rawAuthor]}</p>
            ) : null}
          </div>
          <div className="profile-actions">
            <button
              type="button"
              className={settingsSaveButtonClassName(saveStatus[`rebuild:${profile.rawAuthor}`], true)}
              onClick={() => onRequestAuthorActivityRebuild(profile)}
              disabled={settingsReadOnly || saving === `rebuild:${profile.rawAuthor}` || rebuildRunning}
            >
              {saving === `rebuild:${profile.rawAuthor}` ? "Rebuilding..." : saveStatus[`rebuild:${profile.rawAuthor}`] === "error" ? "Failed" : "Rebuild"}
            </button>
            <button
              type="button"
              className={`${settingsSaveButtonClassName(saveStatus[deleteActivityKey], true)} danger-button`}
              onClick={() => onRequestAuthorActivityDelete(profile)}
              disabled={settingsReadOnly || saving === deleteActivityKey}
            >
              {saving === deleteActivityKey ? "Deleting..." : saveStatus[deleteActivityKey] === "error" ? "Failed" : "Delete"}
            </button>
            <button
              type="button"
              className="primary-button danger-solid-button delete-all-data-solid-button"
              onClick={() => onRequestAuthorDeleteAllActivity(profile)}
              disabled={settingsReadOnly || saving === deleteActivityKey}
            >
              Delete all data
            </button>
          </div>
        </div>
      );
    })}
  </div>
</div>
      </div>
      </div>
      {!settingsReadOnly && bulkActivityDeleteModalOpen ? (
<BulkAllAuthorsActivityDeleteModal
  preset={bulkActivityDeletePreset}
  authorCount={profiles.length}
  saving={saving === "bulk-delete-all-authors"}
  onCancel={() => onBulkActivityDeleteModalOpenChange(false)}
  onDelete={(confirmPhrase) => onExecuteBulkActivityDeleteAllAuthors(confirmPhrase)}
/>
      ) : null}
      {!settingsReadOnly && fullActivityRebuildModalOpen ? (
<FullActivityRebuildModal
  saving={saving === "full-activity-rebuild"}
  onCancel={() => onFullActivityRebuildModalOpenChange(false)}
  onRebuild={(confirmPhrase) => onExecuteFullActivityRebuild(confirmPhrase)}
/>
      ) : null}
      {!settingsReadOnly && pendingAuthorActivityRebuild ? (
<AuthorDeleteConfirm
  profile={pendingAuthorActivityRebuild.profile}
  saving={saving === `rebuild:${pendingAuthorActivityRebuild.profile.rawAuthor}`}
  onCancel={() => onPendingAuthorActivityRebuildChange(null)}
  onDelete={() => onExecuteAuthorActivityRebuild(pendingAuthorActivityRebuild)}
  periodStartDate={pendingAuthorActivityRebuild.startDate}
  periodEndDate={pendingAuthorActivityRebuild.endDate}
  title="Rebuild activity data"
  lead="Review the rebuild scope below, then confirm. Your choice applies only to the dates described."
  description={`This will rebuild derived activity rows and daily aggregates for ${pendingAuthorActivityRebuild.profile.rawAuthor} from ${pendingAuthorActivityRebuild.startDate} through ${pendingAuthorActivityRebuild.endDate} (inclusive). Raw activity data and author profile settings are kept unchanged.`}
  confirmLabel="Rebuild activity for period"
  savingLabel="Rebuilding..."
/>
      ) : null}
      {!settingsReadOnly && pendingAuthorActivityDelete?.mode === "range" ? (
<AuthorDeleteConfirm
  profile={pendingAuthorActivityDelete.profile}
  saving={saving === `delete:${pendingAuthorActivityDelete.profile.rawAuthor}`}
  onCancel={() => onPendingAuthorActivityDeleteChange(null)}
  onDelete={() => onExecuteAuthorActivityDelete(pendingAuthorActivityDelete)}
  periodStartDate={pendingAuthorActivityDelete.startDate}
  periodEndDate={pendingAuthorActivityDelete.endDate}
  description={`This will remove reports, raw activity events, Telegram day/break data, Discord meeting activity in range, calendar marks, status events, and activity statistics for ${pendingAuthorActivityDelete.profile.rawAuthor} from ${pendingAuthorActivityDelete.startDate} through ${pendingAuthorActivityDelete.endDate} (inclusive). The author profile will stay unchanged. Aggregates will be rebuilt for those dates. This action cannot be undone.`}
  confirmLabel="Delete activity for period"
/>
      ) : null}
      {!settingsReadOnly && pendingAuthorActivityDelete?.mode === "all" ? (
<AuthorDeleteAllActivityModal
  profile={pendingAuthorActivityDelete.profile}
  saving={saving === `delete:${pendingAuthorActivityDelete.profile.rawAuthor}`}
  onCancel={() => onPendingAuthorActivityDeleteChange(null)}
  onDelete={() => onExecuteAuthorActivityDelete(pendingAuthorActivityDelete)}
/>
      ) : null}
      {!settingsReadOnly && deleteProfileTarget ? (
<AuthorProfileDeleteModal
  profile={deleteProfileTarget}
  saving={saving === `delete-profile:${deleteProfileTarget.rawAuthor}`}
  deleteError={saveStatus[`delete-profile:${deleteProfileTarget.rawAuthor}`] === "error"}
  onCancel={() => onDeleteProfileTargetChange(null)}
  onDelete={() => onDeleteAuthorProfile(deleteProfileTarget.rawAuthor)}
/>
      ) : null}
</>
  );
}
