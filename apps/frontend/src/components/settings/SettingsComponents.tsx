import React, { useEffect, useState } from "react";
import type { AuthorProfile, MeetingRecordingStatus, SiteUser, SiteUserRole, Summary } from "../../types/dashboard";
import { apiFetch } from "../../api/client";
import { REFRESH_INTERVAL_MS } from "../../constants/dashboard";
import { formatReportMinutes } from "../../utils/reports";
import { formatProfileTimeZoneLabel, formatProfileTimeZoneTitle, formatTimestamp } from "../../pages/pageHelpers";
export function SiteUsersPanel({ currentUser }: { currentUser: SiteUser }) {
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
      <p className="settings-caption">Create dashboard logins, issue temporary passwords, and choose what each person can do on the site.</p>
      <div className="site-user-create-card">
        <label>
          Email
          <input value={newUser.email} onChange={(event) => setNewUser((user) => ({ ...user, email: event.target.value }))} />
        </label>
        <label>
          Display Name
          <input value={newUser.displayName} onChange={(event) => setNewUser((user) => ({ ...user, displayName: event.target.value }))} />
        </label>
        <label>
          Password
          <input
            value={newUser.password}
            onChange={(event) => setNewUser((user) => ({ ...user, password: event.target.value }))}
            type="password"
            minLength={8}
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
      <div className="site-users-table">
        <div className="site-users-head">
          <span>Email</span>
          <span>Name</span>
          <span>Role</span>
          <span>Status</span>
          <span>New Password</span>
          <span>Actions</span>
        </div>
        {users.map((user) => {
          const draft = drafts[user.email] ?? user;
          const deleteKey = `delete:${user.email}`;
          const userDirty = isUserDirty(user);
          return (
            <div className="site-user-row" key={user.email}>
              <strong>{user.email}</strong>
              <input
                value={draft.displayName}
                onChange={(event) => setDrafts((items) => ({ ...items, [user.email]: { ...draft, displayName: event.target.value } }))}
              />
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
                type="password"
                placeholder="Leave unchanged"
                onChange={(event) => setDrafts((items) => ({ ...items, [user.email]: { ...draft, password: event.target.value } as SiteUser }))}
              />
              <div className="profile-actions">
                <button
                  className={settingsSaveButtonClassName(status[user.email], true)}
                  onClick={() => void saveUser(user.email, (draft as SiteUser & { password?: string }).password)}
                  disabled={saving === user.email || !userDirty}
                >
                  {settingsSaveButtonLabel(user.email, saving, status)}
                </button>
                <button
                  className={`${settingsSaveButtonClassName(status[deleteKey], true)} danger-button`}
                  onClick={() => void deleteUser(user.email)}
                  disabled={saving === deleteKey || user.email === currentUser.email}
                >
                  {saving === deleteKey ? "Deleting..." : "Delete"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function AuthorDeleteConfirm({
  profile,
  saving,
  onCancel,
  onDelete
}: {
  profile: AuthorProfile;
  saving: boolean;
  onCancel: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="calendar-modal">
        <h2>Delete all data for {profile.displayName}</h2>
        <p className="calendar-helper">
          This will remove reports, raw activity events, Telegram day/break data, alerts, and activity statistics for this
          author. The author profile, display name, Telegram username, color, and plugin settings will stay unchanged. This action cannot be undone.
        </p>
        <div className="modal-actions">
          <button className="primary-outline-button" onClick={onCancel} disabled={saving}>Cancel</button>
          <button className="primary-button danger-solid-button" onClick={onDelete} disabled={saving}>
            {saving ? "Deleting..." : "Delete all author data"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function AuthorProfileDeleteConfirm({
  profile,
  saving,
  onCancel,
  onDelete
}: {
  profile: AuthorProfile;
  saving: boolean;
  onCancel: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="calendar-modal">
        <h2>Delete profile for {profile.displayName}</h2>
        <p className="calendar-helper">
          This will remove the author profile, Telegram mapping, plugin settings, reports, raw activity events, Telegram day/break data,
          calendar marks, alerts, and activity statistics for this author. This action cannot be undone.
        </p>
        <div className="modal-actions">
          <button className="primary-outline-button" onClick={onCancel} disabled={saving}>Cancel</button>
          <button className="primary-button danger-solid-button" onClick={onDelete} disabled={saving}>
            {saving ? "Deleting..." : "Delete profile and all data"}
          </button>
        </div>
      </div>
    </div>
  );
}

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

