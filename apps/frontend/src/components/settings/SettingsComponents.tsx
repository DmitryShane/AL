import React, { useEffect, useState } from "react";
import type { AuthorProfile, MeetingRecordingStatus, SiteUser, SiteUserRole, Summary } from "../../types/dashboard";
import { apiFetch } from "../../api/client";
import { REFRESH_INTERVAL_MS } from "../../constants/dashboard";
import { formatReportMinutes } from "../../utils/reports";
import { formatProfileTimeZoneLabel, formatProfileTimeZoneTitle, formatTimestamp } from "../../pages/pageHelpers";
import { SiteUserDeleteModal } from "./SiteUserDeleteModal";

/** Table Name column only—when author profile cannot be linked by email/rawAuthor. */
const SITE_USER_TABLE_NAME_FALLBACK: Record<string, string> = {
  "igor.mats@gmail.com": "Igor Mats"
};

function siteUserTableNameFallback(email: string): string | undefined {
  const label = SITE_USER_TABLE_NAME_FALLBACK[email.trim().toLowerCase()];
  const trimmed = label?.trim();

  if (!trimmed) {
    return undefined;
  }

  return trimmed;
}

function authorProfileDisplayNameForSiteEmail(
  email: string,
  profiles: AuthorProfile[],
  drafts: Record<string, AuthorProfile>
): string | undefined {
  const normalizedEmail = email.trim().toLowerCase();

  if (!normalizedEmail) {
    return undefined;
  }

  for (const profile of profiles) {
    const draft = drafts[profile.rawAuthor] ?? profile;
    const profileEmail = (draft.authorEmail ?? profile.authorEmail ?? "").trim().toLowerCase();
    const rawNorm = (draft.rawAuthor ?? profile.rawAuthor ?? "").trim().toLowerCase();

    const matchesByEmail = Boolean(profileEmail) && profileEmail === normalizedEmail;
    const matchesByRawAuthor = Boolean(rawNorm) && rawNorm === normalizedEmail;

    if (!matchesByEmail && !matchesByRawAuthor) {
      continue;
    }

    const displayName = (draft.displayName ?? "").trim();

    if (displayName) {
      return displayName;
    }

    return undefined;
  }

  return undefined;
}

export function SiteUsersPanel({
  currentUser,
  authorProfiles = [],
  authorProfileDrafts = {}
}: {
  currentUser: SiteUser;
  authorProfiles?: AuthorProfile[];
  authorProfileDrafts?: Record<string, AuthorProfile>;
}) {
  const canManageUsers = currentUser.role === "admin";
  const [users, setUsers] = useState<SiteUser[]>([]);
  const [drafts, setDrafts] = useState<Record<string, SiteUser>>({});
  const [newUser, setNewUser] = useState<SiteUser & { password: string }>({
    email: "",
    displayName: "",
    role: "viewer",
    active: true,
    password: ""
  });
  const [saving, setSaving] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, "saved" | "error" | undefined>>({});
  const [pendingDeleteEmail, setPendingDeleteEmail] = useState<string | null>(null);

  async function loadUsers() {
    if (!canManageUsers) {
      return;
    }

    const response = await apiFetch("/api/v1/site-users");

    if (response.ok) {
      const payload = await response.json();
      const nextUsers = payload.users ?? [];
      setUsers(nextUsers);
      setDrafts(Object.fromEntries(nextUsers.map((user: SiteUser) => [user.email, user])));
    }
  }

  useEffect(() => {
    void loadUsers();
  }, [canManageUsers]);

  async function saveUser(email: string, password?: string) {
    const draft = drafts[email];

    if (!draft) {
      return;
    }

    await persistUser(email, { ...draft, password });
  }

  async function createUser() {
    await persistUser("newUser", newUser);
  }

  async function persistUser(key: string, user: SiteUser & { password?: string }) {
    setSaving(key);
    setStatus((items) => ({ ...items, [key]: undefined }));

    try {
      const response = await apiFetch("/api/v1/site-users", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: user.email,
          displayName: user.displayName,
          role: user.role,
          active: user.active,
          password: user.password || undefined
        })
      });

      if (!response.ok) {
        throw new Error("User save failed");
      }

      if (key === "newUser") {
        setNewUser({ email: "", displayName: "", role: "viewer", active: true, password: "" });
      }

      setStatus((items) => ({ ...items, [key]: "saved" }));
      await loadUsers();
    } catch {
      setStatus((items) => ({ ...items, [key]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setStatus((items) => ({ ...items, [key]: undefined }));
      }, 2500);
    }
  }

  async function deleteUser(email: string) {
    setSaving(`delete:${email}`);
    setStatus((items) => ({ ...items, [`delete:${email}`]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/site-users/${encodeURIComponent(email)}`, { method: "DELETE" });

      if (!response.ok) {
        throw new Error("User delete failed");
      }

      setPendingDeleteEmail(null);
      setStatus((items) => ({ ...items, [`delete:${email}`]: "saved" }));
      await loadUsers();
    } catch {
      setStatus((items) => ({ ...items, [`delete:${email}`]: "error" }));
    } finally {
      setSaving(null);
    }
  }

  function isUserDirty(user: SiteUser) {
    const draft = drafts[user.email] as SiteUser & { password?: string } | undefined;

    if (!draft) {
      return false;
    }

    return (
      (draft.displayName ?? "") !== (user.displayName ?? "") ||
      draft.role !== user.role ||
      Boolean(draft.active) !== Boolean(user.active) ||
      Boolean(draft.password)
    );
  }

  const pendingDeleteUser = pendingDeleteEmail ? users.find((userItem) => userItem.email === pendingDeleteEmail) : undefined;

  if (!canManageUsers) {
    return (
      <div className="panel">
        <h2>Site Users</h2>
        <p className="settings-caption">Only admins can create users, reset passwords, and change access rights.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>Site Users</h2>
      <p className="settings-caption">
        Create dashboard logins, issue temporary passwords, and choose what each person can do on the site.
        Existing passwords are stored as hashes and cannot be shown; enter a new value in the password column only when resetting someone&apos;s login.
      </p>
      <div className="site-user-create-card">
        <label>
          Display Name
          <input value={newUser.displayName} onChange={(event) => setNewUser((user) => ({ ...user, displayName: event.target.value }))} />
        </label>
        <label>
          Email
          <input value={newUser.email} onChange={(event) => setNewUser((user) => ({ ...user, email: event.target.value }))} />
        </label>
        <label>
          Password
          <input
            value={newUser.password}
            onChange={(event) => setNewUser((user) => ({ ...user, password: event.target.value }))}
            type="text"
            minLength={8}
            spellCheck={false}
            autoComplete="new-password"
          />
        </label>
        <label>
          Role
          <select value={newUser.role} onChange={(event) => setNewUser((user) => ({ ...user, role: event.target.value as SiteUserRole }))}>
            <option value="admin">Admin</option>
            <option value="editor">Editor</option>
            <option value="viewer">Viewer</option>
          </select>
        </label>
        <button
          className={settingsSaveButtonClassName(status.newUser)}
          onClick={() => void createUser()}
          disabled={saving === "newUser" || !newUser.email.trim() || newUser.password.length < 8}
        >
          {saving === "newUser" ? "Creating..." : status.newUser === "saved" ? "Created" : status.newUser === "error" ? "Failed" : "Add user"}
        </button>
      </div>
      <div className="profile-table-shell">
        <div className="site-users-table">
          <div className="site-users-head">
            <span>Name</span>
            <span>Email</span>
            <span>Role</span>
            <span>Status</span>
            <span>New Password</span>
            <span>Actions</span>
          </div>
          {users.map((user) => {
          const draft = drafts[user.email] ?? user;
          const deleteKey = `delete:${user.email}`;
          const userDirty = isUserDirty(user);
          const profileLinkedName = authorProfileDisplayNameForSiteEmail(user.email, authorProfiles, authorProfileDrafts);
          const fallbackTableName = siteUserTableNameFallback(user.email);
          const nameCellText =
            profileLinkedName ??
            fallbackTableName ??
            ((draft.displayName ?? "").trim() ? draft.displayName.trim() : "—");
          const nameCellTitle =
            profileLinkedName ??
            fallbackTableName ??
            ((draft.displayName ?? "").trim() ? draft.displayName.trim() : undefined);
          return (
            <div className="site-user-row" key={user.email}>
              <span className="site-user-name-cell" title={nameCellTitle}>
                {nameCellText}
              </span>
              <span className="profile-author-cell site-user-email-cell" title={user.email}>
                <input
                  type="text"
                  value={draft.displayName}
                  placeholder="Display name"
                  aria-label={`Display name for ${user.email}`}
                  spellCheck={false}
                  autoComplete="name"
                  onChange={(event) => setDrafts((items) => ({ ...items, [user.email]: { ...draft, displayName: event.target.value } }))}
                />
              </span>
              <select
                value={draft.role}
                onChange={(event) => setDrafts((items) => ({ ...items, [user.email]: { ...draft, role: event.target.value as SiteUserRole } }))}
              >
                <option value="admin">Admin</option>
                <option value="editor">Editor</option>
                <option value="viewer">Viewer</option>
              </select>
              <label className="checkbox-cell">
                <input
                  type="checkbox"
                  checked={draft.active}
                  onChange={(event) => setDrafts((items) => ({ ...items, [user.email]: { ...draft, active: event.target.checked } }))}
                />
                Active
              </label>
              <input
                type="text"
                value={(draft as SiteUser & { password?: string }).password ?? ""}
                placeholder="Optional — new password"
                title="Stored passwords are hashed and cannot be displayed. Enter a new password here to reset."
                spellCheck={false}
                autoComplete="new-password"
                onChange={(event) =>
                  setDrafts((items) => ({
                    ...items,
                    [user.email]: { ...draft, password: event.target.value } as SiteUser & { password?: string }
                  }))
                }
              />
              <div className="profile-actions">
                <button
                  className={settingsSaveButtonClassName(status[user.email], true)}
                  onClick={() => void saveUser(user.email, (draft as SiteUser & { password?: string }).password)}
                  disabled={saving === user.email || !userDirty}
                >
                  {settingsSaveButtonLabel(user.email, saving, status)}
                </button>
                {user.email.trim().toLowerCase() !== currentUser.email.trim().toLowerCase() ? (
                  <button
                    type="button"
                    className="primary-button danger-solid-button delete-all-data-solid-button"
                    onClick={() => setPendingDeleteEmail(user.email)}
                    disabled={saving === deleteKey}
                  >
                    Delete
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
        </div>
      </div>
      {pendingDeleteEmail && pendingDeleteUser ? (
        <SiteUserDeleteModal
          user={pendingDeleteUser}
          saving={saving === `delete:${pendingDeleteEmail}`}
          deleteError={status[`delete:${pendingDeleteEmail}`] === "error"}
          onCancel={() => setPendingDeleteEmail(null)}
          onDelete={() => void deleteUser(pendingDeleteUser.email)}
        />
      ) : null}
    </div>
  );
}

export { AuthorDeleteConfirm } from "./AuthorDeleteConfirmModal";
export type { AuthorDeleteConfirmProps } from "./AuthorDeleteConfirmModal";

export { AuthorProfileDeleteModal } from "./AuthorProfileDeleteModal";

export { SiteUserDeleteModal } from "./SiteUserDeleteModal";

function settingsSaveButtonLabel(key: string, saving: string | null, statuses: Record<string, "saved" | "error" | undefined>) {
  if (saving === key) {
    return "Saving...";
  }

  if (statuses[key] === "saved") {
    return "Saved";
  }

  if (statuses[key] === "error") {
    return "Failed";
  }

  return "Save";
}

function dashboardRefreshIntervalMs(summary: Summary | null) {
  const seconds = summary?.intervalSettings.defaultSendIntervalSeconds ?? REFRESH_INTERVAL_MS / 1000;
  return Math.max(1000, seconds * 1000);
}

function formatSiteRole(role: SiteUserRole) {
  if (role === "admin") {
    return "Admin";
  }

  if (role === "editor") {
    return "Editor";
  }

  return "Viewer";
}

function settingsSaveButtonClassName(status: "saved" | "error" | undefined, outline = false) {
  const baseClassName = outline ? "primary-outline-button" : "primary-button";

  if (status === "saved") {
    return `${baseClassName} save-success`;
  }

  if (status === "error") {
    return `${baseClassName} save-error`;
  }

  return baseClassName;
}

function meetingRecordingStatusLabel(recording: MeetingRecordingStatus) {
  if (recording.status === "recording") {
    return "Recording now";
  }

  if (recording.status === "uploading_audio") {
    return "Uploading audio to backend";
  }

  if (recording.status === "compressing_audio") {
    return "Compressing audio";
  }

  if (recording.status === "transcribing_openai") {
    return "Transcribing with OpenAI";
  }

  if (recording.status === "summarizing_openai") {
    return "Summarizing with OpenAI";
  }

  if (recording.status === "waiting_for_telegram" || recording.status === "summary_pending") {
    return "Summary created, waiting for Telegram";
  }

  if (recording.status === "telegram_claimed" || recording.status === "summary_claimed") {
    return "Telegram is sending the summary";
  }

  if (recording.status === "telegram_sent") {
    return "Summary sent to Telegram";
  }

  if (recording.status === "summary_failed") {
    return "Summary failed";
  }

  if (recording.status === "recording_failed") {
    return "Recording upload failed";
  }

  if (recording.status.startsWith("skipped_")) {
    return `Skipped: ${recording.status.replace("skipped_", "").replaceAll("_", " ")}`;
  }

  return recording.status.replaceAll("_", " ");
}

function meetingRecordingDetail(recording: MeetingRecordingStatus) {
  const people = recording.participantNames?.length ? recording.participantNames.join(", ") : "No participants";
  const duration = recording.durationSeconds ? `, ${formatReportMinutes(recording.durationSeconds)}` : "";
  const recipient = meetingRecordingRecipientLabel(recording);
  const sentAt = recording.telegramSentAt ? `, sent ${formatTimestamp(recording.telegramSentAt)}` : "";
  const startedAt = recording.startedAt ? `Started ${formatTimestamp(recording.startedAt)}` : "Started time unknown";
  const updatedAt = recording.updatedAt ? ` Last update ${formatTimestamp(recording.updatedAt)}.` : "";
  const audioStats = meetingRecordingAudioStats(recording);

  return `${people}${duration}. ${startedAt}${recipient}${sentAt}.${audioStats}${updatedAt}`;
}

function meetingRecordingRecipientLabel(recording: MeetingRecordingStatus) {
  if (!recording.recipient) {
    return "";
  }

  if (recording.recipient.kind === "private") {
    return `, recipient ${recording.recipient.label || "private chat"}`;
  }

  return ", recipient work chat";
}

function meetingRecordingAudioStats(recording: MeetingRecordingStatus) {
  if (!recording.audioFrameCount && !recording.audioSizeBytes && !recording.corruptedPacketCount && !recording.audioQualityStatus) {
    return "";
  }

  const quality = recording.audioQualityStatus ? `, quality ${recording.audioQualityStatus}` : "";
  const mixedUsers = recording.mixedUserCount ? `, mixed users ${recording.mixedUserCount}` : "";
  const padding = recording.silencePaddingFrameCount ? `, padded frames ${recording.silencePaddingFrameCount}` : "";
  const unknown = recording.unknownSourceFrameCount ? `, unknown frames ${recording.unknownSourceFrameCount}` : "";
  const listenErrors = recording.listenErrorCount ? `, listen errors ${recording.listenErrorCount}` : "";
  const sizeMb = recording.audioSizeBytes ? `, file ${(recording.audioSizeBytes / 1024 / 1024).toFixed(2)} MB` : "";
  return ` Audio frames: ${recording.nonSilentFrameCount ?? 0}/${recording.audioFrameCount ?? 0}, corrupted packets: ${recording.corruptedPacketCount ?? 0}${quality}${mixedUsers}${padding}${unknown}${listenErrors}${sizeMb}.`;
}

function emptyAuthorProfile(): AuthorProfile {
  return {
    rawAuthor: "",
    displayName: "",
    team: "",
    telegramUsername: "",
    discordUserId: "",
    discordUsername: "",
    pluginEnabled: true,
    autoBreakEnabled: false,
    autoBreakEffectiveDate: "",
    authorColor: "#13a37b"
  };
}

function authorProfilePayload(profile: AuthorProfile) {
  return {
    rawAuthor: profile.rawAuthor,
    displayName: profile.displayName,
    team: profile.team ?? "",
    telegramUsername: profile.telegramUsername ?? "",
    discordUserId: profile.discordUserId ?? "",
    discordUsername: profile.discordUsername ?? "",
    pluginEnabled: profile.pluginEnabled ?? true,
    autoBreakEnabled: profile.autoBreakEnabled ?? false,
    autoBreakEffectiveDate: profile.autoBreakEffectiveDate ?? "",
    authorColor: profile.authorColor ?? "#13a37b"
  };
}

function autoBreakScheduleLabel(profile: AuthorProfile) {
  if (!profile.autoBreakEnabled) {
    return "Auto break is off";
  }

  if (profile.autoBreakEffectiveDate) {
    return `Starts ${profile.autoBreakEffectiveDate}`;
  }

  return "Starts next work day after save";
}

function normalizeAuthorInput(value: string) {
  return value.trim().normalize("NFC");
}

