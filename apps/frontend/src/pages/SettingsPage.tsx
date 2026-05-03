import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { MEETING_AUDIO_RETENTION_OPTIONS, MEETING_SUMMARY_LANGUAGES, SETTINGS_TAB_STORAGE_KEY } from "../constants/dashboard";
import type { AuthorProfile, MeetingRecordingStatus, SettingsTab, SiteUser, Summary } from "../types/dashboard";
import { autoBreakScheduleLabel, authorProfilePayload, emptyAuthorProfile, formatProfileTimeZoneLabel, formatProfileTimeZoneTitle, loadSavedSettingsTab, meetingRecordingDetail, meetingRecordingStatusLabel, normalizeAuthorInput, settingsSaveButtonClassName, settingsSaveButtonLabel } from "./pageHelpers";
import { AuthorDeleteConfirm, AuthorProfileDeleteConfirm, SiteUsersPanel } from "../components/settings/SettingsComponents";
export function SettingsPage({
  summary,
  currentUser,
  onSaved
}: {
  summary: Summary | null;
  currentUser: SiteUser;
  onSaved: () => void;
}) {
  const profiles = summary?.activitySummary.profiles ?? [];
  const aliases = summary?.activitySummary.authorAliases ?? [];
  const [settingsTab, setSettingsTabState] = useState<SettingsTab>(() => loadSavedSettingsTab());
  const [drafts, setDrafts] = useState<Record<string, AuthorProfile>>({});
  const [globalInterval, setGlobalInterval] = useState(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
  const [idleThreshold, setIdleThreshold] = useState(String(intervalSettingsIdleThreshold(summary)));
  const [pluginIngestEnabled, setPluginIngestEnabled] = useState(summary?.intervalSettings.pluginIngestEnabled ?? true);
  const [discordAutoAfkTimeout, setDiscordAutoAfkTimeout] = useState(String(summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600));
  const [telegramOnlinePromptDelayMinutes, setTelegramOnlinePromptDelayMinutes] = useState(
    String(intervalSettingsTelegramOnlinePromptMinutes(summary))
  );
  const [meetingSummariesEnabled, setMeetingSummariesEnabled] = useState(Boolean(summary?.discordSettings.meetingSummariesEnabled));
  const [meetingSummaryMinParticipants, setMeetingSummaryMinParticipants] = useState(String(summary?.discordSettings.meetingSummaryMinParticipants ?? 2));
  const [meetingSummaryMinDuration, setMeetingSummaryMinDuration] = useState(String(summary?.discordSettings.meetingSummaryMinDurationSeconds ?? 120));
  const [meetingSummaryLanguage, setMeetingSummaryLanguage] = useState(summary?.discordSettings.meetingSummaryLanguage ?? "English");
  const [meetingSummaryRecipient, setMeetingSummaryRecipient] = useState(summary?.discordSettings.meetingSummaryRecipient ?? "work_chat");
  const [meetingAudioRetention, setMeetingAudioRetention] = useState(String(summary?.discordSettings.meetingAudioRetentionSeconds ?? 0));
  const [meetingSummaryPrompt, setMeetingSummaryPrompt] = useState(summary?.discordSettings.meetingSummaryPrompt ?? "");
  const [saving, setSaving] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, "saved" | "error" | undefined>>({});
  const [aliasError, setAliasError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<AuthorProfile | null>(null);
  const [deleteProfileTarget, setDeleteProfileTarget] = useState<AuthorProfile | null>(null);
  const [newProfile, setNewProfile] = useState<AuthorProfile>(() => emptyAuthorProfile());
  const [aliasSource, setAliasSource] = useState("");
  const [aliasTarget, setAliasTarget] = useState("");
  const [meetingRecordings, setMeetingRecordings] = useState<MeetingRecordingStatus[]>([]);
  const [meetingRecordingsError, setMeetingRecordingsError] = useState("");

  useEffect(() => {
    const nextDrafts: Record<string, AuthorProfile> = {};

    for (const profile of profiles) {
      nextDrafts[profile.rawAuthor] = { ...profile };
    }

    setDrafts(nextDrafts);
    setGlobalInterval(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
    setIdleThreshold(String(intervalSettingsIdleThreshold(summary)));
    setPluginIngestEnabled(summary?.intervalSettings.pluginIngestEnabled ?? true);
    setDiscordAutoAfkTimeout(String(summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600));
    setTelegramOnlinePromptDelayMinutes(String(intervalSettingsTelegramOnlinePromptMinutes(summary)));
    setMeetingSummariesEnabled(Boolean(summary?.discordSettings.meetingSummariesEnabled));
    setMeetingSummaryMinParticipants(String(summary?.discordSettings.meetingSummaryMinParticipants ?? 2));
    setMeetingSummaryMinDuration(String(summary?.discordSettings.meetingSummaryMinDurationSeconds ?? 120));
    setMeetingSummaryLanguage(summary?.discordSettings.meetingSummaryLanguage ?? "English");
    setMeetingSummaryRecipient(summary?.discordSettings.meetingSummaryRecipient ?? "work_chat");
    setMeetingAudioRetention(String(summary?.discordSettings.meetingAudioRetentionSeconds ?? 0));
    setMeetingSummaryPrompt(summary?.discordSettings.meetingSummaryPrompt ?? "");
  }, [summary]);

  useEffect(() => {
    if (!aliasTarget && profiles.length) {
      setAliasTarget(profiles[0].rawAuthor);
    }
  }, [aliasTarget, profiles]);

  useEffect(() => {
    if (settingsTab !== "meetingSummaries") {
      return;
    }

    let cancelled = false;

    async function loadMeetingRecordings() {
      try {
        const response = await apiFetch("/api/v1/discord/meeting-recordings/recent");

        if (!response.ok) {
          throw new Error("Meeting recording status load failed");
        }

        const data = await response.json() as { recordings?: MeetingRecordingStatus[] };

        if (!cancelled) {
          setMeetingRecordings(data.recordings ?? []);
          setMeetingRecordingsError("");
        }
      } catch {
        if (!cancelled) {
          setMeetingRecordingsError("Could not load meeting summary status.");
        }
      }
    }

    void loadMeetingRecordings();
    const intervalId = window.setInterval(() => void loadMeetingRecordings(), 5000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [settingsTab]);

  function setSettingsTab(tab: SettingsTab) {
    setSettingsTabState(tab);
    localStorage.setItem(SETTINGS_TAB_STORAGE_KEY, tab);
  }

  const meetingSummarySettingsDirty =
    meetingSummariesEnabled !== Boolean(summary?.discordSettings.meetingSummariesEnabled) ||
    meetingSummaryMinParticipants !== String(summary?.discordSettings.meetingSummaryMinParticipants ?? 2) ||
    meetingSummaryMinDuration !== String(summary?.discordSettings.meetingSummaryMinDurationSeconds ?? 120) ||
    meetingSummaryLanguage !== (summary?.discordSettings.meetingSummaryLanguage ?? "English") ||
    meetingSummaryRecipient !== (summary?.discordSettings.meetingSummaryRecipient ?? "work_chat") ||
    meetingAudioRetention !== String(summary?.discordSettings.meetingAudioRetentionSeconds ?? 0) ||
    meetingSummaryPrompt !== (summary?.discordSettings.meetingSummaryPrompt ?? "");
  const discordSettingsDirty = discordAutoAfkTimeout !== String(summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600);
  const telegramPromptSettingsDirty =
    telegramOnlinePromptDelayMinutes !== String(intervalSettingsTelegramOnlinePromptMinutes(summary));

  async function saveProfile(rawAuthor: string) {
    const profile = drafts[rawAuthor];

    if (!profile) {
      return;
    }

    setSaving(rawAuthor);
    setSaveStatus((items) => ({ ...items, [rawAuthor]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(authorProfilePayload(profile))
      });

      if (!response.ok) {
        throw new Error("Profile save failed");
      }

      setSaveStatus((items) => ({ ...items, [rawAuthor]: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, [rawAuthor]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [rawAuthor]: undefined }));
      }, 2500);
    }
  }

  async function createProfile() {
    const rawAuthor = normalizeAuthorInput(newProfile.rawAuthor);

    if (!rawAuthor) {
      setSaveStatus((items) => ({ ...items, newProfile: "error" }));
      return;
    }

    const profile = {
      ...newProfile,
      rawAuthor,
      displayName: (newProfile.displayName || rawAuthor).trim()
    };

    setSaving("newProfile");
    setSaveStatus((items) => ({ ...items, newProfile: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(authorProfilePayload(profile))
      });

      if (!response.ok) {
        throw new Error("Profile create failed");
      }

      setNewProfile(emptyAuthorProfile());
      setSaveStatus((items) => ({ ...items, newProfile: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, newProfile: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, newProfile: undefined }));
      }, 2500);
    }
  }

  async function saveInterval() {
    setSaving("interval");
    setSaveStatus((items) => ({ ...items, interval: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/intervals`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          defaultSendIntervalSeconds: Number(globalInterval),
          idleThresholdSeconds: Number(idleThreshold),
          pluginIngestEnabled
        })
      });

      if (!response.ok) {
        throw new Error("Interval save failed");
      }

      setSaveStatus((items) => ({ ...items, interval: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, interval: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, interval: undefined }));
      }, 2500);
    }
  }

  async function saveTelegramPromptSettings() {
    setSaving("telegramPrompt");
    setSaveStatus((items) => ({ ...items, telegramPrompt: undefined }));

    try {
      const minutes = Number(telegramOnlinePromptDelayMinutes);

      if (!Number.isFinite(minutes)) {
        throw new Error("Invalid minutes");
      }

      const response = await apiFetch(`/api/v1/settings/intervals`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          telegramOnlinePromptDelayMinutes: minutes,
        }),
      });

      if (!response.ok) {
        throw new Error("Telegram prompt settings save failed");
      }

      setSaveStatus((items) => ({ ...items, telegramPrompt: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, telegramPrompt: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, telegramPrompt: undefined }));
      }, 2500);
    }
  }

  async function saveDiscordSettings() {
    setSaving("discord");
    setSaveStatus((items) => ({ ...items, discord: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/discord`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meetingAutoAfkTimeoutSeconds: Number(discordAutoAfkTimeout),
          meetingSummariesEnabled,
          meetingSummaryMinParticipants: Number(meetingSummaryMinParticipants),
          meetingSummaryMinDurationSeconds: Number(meetingSummaryMinDuration),
          meetingSummaryLanguage,
          meetingSummaryRecipient,
          meetingAudioRetentionSeconds: Number(meetingAudioRetention),
          meetingSummaryPrompt
        })
      });

      if (!response.ok) {
        throw new Error("Discord settings save failed");
      }

      setSaveStatus((items) => ({ ...items, discord: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, discord: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, discord: undefined }));
      }, 2500);
    }
  }

  async function deleteAuthorData(rawAuthor: string) {
    const deleteKey = `delete:${rawAuthor}`;
    setSaving(deleteKey);
    setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/${encodeURIComponent(rawAuthor)}/data`, {
        method: "DELETE"
      });

      if (!response.ok) {
        throw new Error("Author data delete failed");
      }

      setDeleteTarget(null);
      setSaveStatus((items) => ({ ...items, [deleteKey]: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, [deleteKey]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));
      }, 2500);
    }
  }

  async function deleteAuthorProfile(rawAuthor: string) {
    const deleteKey = `delete-profile:${rawAuthor}`;
    setSaving(deleteKey);
    setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/${encodeURIComponent(rawAuthor)}/profile`, {
        method: "DELETE"
      });

      if (!response.ok) {
        throw new Error("Author profile delete failed");
      }

      setDeleteProfileTarget(null);
      setSaveStatus((items) => ({ ...items, [deleteKey]: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, [deleteKey]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));
      }, 2500);
    }
  }

  async function saveAuthorAlias() {
    const sourceRawAuthor = normalizeAuthorInput(aliasSource);
    const targetRawAuthor = normalizeAuthorInput(aliasTarget);

    if (!sourceRawAuthor || !targetRawAuthor || sourceRawAuthor === targetRawAuthor) {
      setSaveStatus((items) => ({ ...items, authorAlias: "error" }));
      return;
    }

    setSaving("authorAlias");
    setAliasError("");
    setSaveStatus((items) => ({ ...items, authorAlias: undefined }));

    try {
      const response = await apiFetch("/api/v1/authors/aliases", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceRawAuthor, targetRawAuthor })
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(String(payload.detail || "Alias save failed"));
      }

      setAliasSource("");
      setSaveStatus((items) => ({ ...items, authorAlias: "saved" }));
      onSaved();
    } catch (error) {
      setAliasError(error instanceof Error ? error.message : "Alias save failed");
      setSaveStatus((items) => ({ ...items, authorAlias: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, authorAlias: undefined }));
      }, 2500);
    }
  }

  async function deleteAuthorAlias(sourceRawAuthor: string) {
    const deleteKey = `alias-delete:${sourceRawAuthor}`;
    setSaving(deleteKey);
    setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/aliases/${encodeURIComponent(sourceRawAuthor)}`, { method: "DELETE" });

      if (!response.ok) {
        throw new Error("Alias delete failed");
      }

      setSaveStatus((items) => ({ ...items, [deleteKey]: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, [deleteKey]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));
      }, 2500);
    }
  }

  function isProfileDirty(profile: AuthorProfile) {
    const draft = drafts[profile.rawAuthor] ?? profile;

    return (
      (draft.displayName ?? "") !== (profile.displayName ?? "") ||
      (draft.team ?? "") !== (profile.team ?? "") ||
      (draft.telegramUsername ?? "") !== (profile.telegramUsername ?? "") ||
      (draft.discordUserId ?? "") !== (profile.discordUserId ?? "") ||
      (draft.discordUsername ?? "") !== (profile.discordUsername ?? "") ||
      (draft.authorColor ?? "") !== (profile.authorColor ?? "") ||
      (draft.pluginEnabled ?? true) !== (profile.pluginEnabled ?? true) ||
      (draft.autoBreakEnabled ?? false) !== (profile.autoBreakEnabled ?? false)
    );
  }

  const savedGlobalInterval = String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300);
  const savedIdleThreshold = String(intervalSettingsIdleThreshold(summary));
  const savedPluginIngestEnabled = summary?.intervalSettings.pluginIngestEnabled ?? true;
  const isIntervalSettingsDirty =
    globalInterval !== savedGlobalInterval ||
    idleThreshold !== savedIdleThreshold ||
    pluginIngestEnabled !== savedPluginIngestEnabled;

  return (
    <section className="page-section settings-layout">
      <div className="settings-tabs">
        <button className={settingsTab === "general" ? "active" : ""} onClick={() => setSettingsTab("general")}>General</button>
        <button className={settingsTab === "authors" ? "active" : ""} onClick={() => setSettingsTab("authors")}>Author Profiles</button>
        <button className={settingsTab === "autoBreak" ? "active" : ""} onClick={() => setSettingsTab("autoBreak")}>Auto Break</button>
        <button className={settingsTab === "redirects" ? "active" : ""} onClick={() => setSettingsTab("redirects")}>Author Redirects</button>
        <button className={settingsTab === "discord" ? "active" : ""} onClick={() => setSettingsTab("discord")}>Discord</button>
        <button className={settingsTab === "telegram" ? "active" : ""} onClick={() => setSettingsTab("telegram")}>Telegram</button>
        <button className={settingsTab === "meetingSummaries" ? "active" : ""} onClick={() => setSettingsTab("meetingSummaries")}>Meeting Summaries</button>
        <button className={settingsTab === "users" ? "active" : ""} onClick={() => setSettingsTab("users")}>Site Users</button>
      </div>
      {settingsTab === "general" ? (
        <div className="panel">
          <h2>Send Interval</h2>
          <div className="settings-row">
            <label>
              Global interval, sec
              <input value={globalInterval} onChange={(event) => setGlobalInterval(event.target.value)} type="number" min="30" />
            </label>
            <label>
              Idle threshold, sec
              <input value={idleThreshold} onChange={(event) => setIdleThreshold(event.target.value)} type="number" min="30" />
            </label>
            <label className="checkbox-cell plugin-ingest-toggle">
              <input
                type="checkbox"
                checked={pluginIngestEnabled}
                onChange={(event) => setPluginIngestEnabled(event.target.checked)}
              />
              Plugin reports: {pluginIngestEnabled ? "On" : "Off"}
            </label>
            <button className={settingsSaveButtonClassName(saveStatus.interval)} onClick={() => void saveInterval()} disabled={saving === "interval" || !isIntervalSettingsDirty}>
              {settingsSaveButtonLabel("interval", saving, saveStatus)}
            </button>
          </div>
        </div>
      ) : settingsTab === "autoBreak" ? (
        <div className="panel">
          <h2>Auto Break</h2>
          <p className="settings-caption">
            Assign authors whose first idle time during a work day should count as break time until the legal 60 minute break is filled.
          </p>
          <div className="auto-break-list">
            {profiles.map((profile) => {
              const draft = drafts[profile.rawAuthor] ?? profile;
              const profileDirty = isProfileDirty(profile);
              return (
                <div className="auto-break-row" key={profile.rawAuthor}>
                  <div>
                    <strong>{profile.displayName || profile.rawAuthor}</strong>
                    <small>{profile.rawAuthor}</small>
                    <small>{autoBreakScheduleLabel(draft)}</small>
                  </div>
                  <label className="checkbox-cell">
                    <input
                      type="checkbox"
                      checked={draft.autoBreakEnabled ?? false}
                      onChange={(event) =>
                        setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, autoBreakEnabled: event.target.checked } }))
                      }
                    />
                    Auto break
                  </label>
                  <button
                    className={settingsSaveButtonClassName(saveStatus[profile.rawAuthor], true)}
                    onClick={() => void saveProfile(profile.rawAuthor)}
                    disabled={saving === profile.rawAuthor || !profileDirty}
                  >
                    {settingsSaveButtonLabel(profile.rawAuthor, saving, saveStatus)}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ) : settingsTab === "redirects" ? (
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
                onChange={(event) => setAliasSource(event.target.value)}
                list="author-alias-source-list"
                placeholder="Unknown User"
              />
              <datalist id="author-alias-source-list">
                {profiles.map((profile) => (
                  <option value={profile.rawAuthor} key={profile.rawAuthor} />
                ))}
              </datalist>
            </label>
            <label>
              Target profile
              <select value={aliasTarget} onChange={(event) => setAliasTarget(event.target.value)}>
                {profiles.map((profile) => (
                  <option value={profile.rawAuthor} key={profile.rawAuthor}>{profile.displayName || profile.rawAuthor}</option>
                ))}
              </select>
            </label>
            <button
              className={settingsSaveButtonClassName(saveStatus.authorAlias)}
              onClick={() => void saveAuthorAlias()}
              disabled={saving === "authorAlias" || !aliasSource.trim() || !aliasTarget.trim()}
            >
              {saving === "authorAlias" ? "Assigning..." : saveStatus.authorAlias === "saved" ? "Assigned" : saveStatus.authorAlias === "error" ? "Failed" : "Assign"}
            </button>
          </div>
          {aliasError ? <p className="notice error">{aliasError}</p> : null}
          <div className="alias-list">
            {aliases.length ? (
              aliases.map((alias) => {
                const target = profiles.find((profile) => profile.rawAuthor === alias.targetRawAuthor);
                const deleteKey = `alias-delete:${alias.sourceRawAuthor}`;
                return (
                  <div className="alias-row" key={alias.sourceRawAuthor}>
                    <span><strong>{alias.sourceRawAuthor}</strong> redirects to <strong>{target?.displayName || alias.targetRawAuthor}</strong></span>
                    <button
                      className={`${settingsSaveButtonClassName(saveStatus[deleteKey], true)} danger-button`}
                      onClick={() => void deleteAuthorAlias(alias.sourceRawAuthor)}
                      disabled={saving === deleteKey}
                    >
                      {saving === deleteKey ? "Deleting..." : saveStatus[deleteKey] === "error" ? "Failed" : "Delete"}
                    </button>
                  </div>
                );
              })
            ) : (
              <p className="empty">No redirects yet.</p>
            )}
          </div>
        </div>
      ) : settingsTab === "authors" ? (
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
              onChange={(event) => setNewProfile((profile) => ({ ...profile, rawAuthor: event.target.value }))}
              placeholder="Git user.name"
            />
          </label>
          <label>
            Display Name
            <input
              value={newProfile.displayName}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, displayName: event.target.value }))}
              placeholder="Shown on dashboard"
            />
          </label>
          <label>
            Team
            <input
              value={newProfile.team ?? ""}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, team: event.target.value }))}
              placeholder="Team"
            />
          </label>
          <label>
            Telegram
            <input
              value={newProfile.telegramUsername ?? ""}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, telegramUsername: event.target.value }))}
              placeholder="@username"
            />
          </label>
          <label>
            Discord ID
            <input
              value={newProfile.discordUserId ?? ""}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, discordUserId: event.target.value }))}
              placeholder="User ID"
            />
          </label>
          <label>
            Discord Name
            <input
              value={newProfile.discordUsername ?? ""}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, discordUsername: event.target.value }))}
              placeholder="username"
            />
          </label>
          <label>
            Color
            <input
              type="color"
              value={newProfile.authorColor ?? "#13a37b"}
              onChange={(event) => setNewProfile((profile) => ({ ...profile, authorColor: event.target.value }))}
            />
          </label>
          <label>
            Plugin
            <span className="checkbox-cell">
              <input
                type="checkbox"
                checked={newProfile.pluginEnabled ?? true}
                onChange={(event) => setNewProfile((profile) => ({ ...profile, pluginEnabled: event.target.checked }))}
              />
              Enabled
            </span>
          </label>
          <button
            className={settingsSaveButtonClassName(saveStatus.newProfile)}
            onClick={() => void createProfile()}
            disabled={saving === "newProfile" || !newProfile.rawAuthor.trim()}
          >
            {saving === "newProfile" ? "Creating..." : saveStatus.newProfile === "saved" ? "Created" : saveStatus.newProfile === "error" ? "Failed" : "Add profile"}
          </button>
        </div>
        <div className="profile-table">
          <div className="profile-table-head">
            <span>Raw Author</span>
            <span>Display Name</span>
            <span>Team</span>
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
            const deleteKey = `delete:${profile.rawAuthor}`;
            const deleteProfileKey = `delete-profile:${profile.rawAuthor}`;
            return (
              <div className="profile-row" key={profile.rawAuthor}>
                <span className="profile-author-cell" title={profile.authorEmail || profile.rawAuthor}>
                  <strong>{profile.rawAuthor}</strong>
                  <small>{profile.authorEmail || "-"}</small>
                </span>
                <input
                  value={draft.displayName}
                  onChange={(event) => setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, displayName: event.target.value } }))}
                />
                <input
                  value={draft.team ?? ""}
                  onChange={(event) => setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, team: event.target.value } }))}
                />
                <input
                  value={draft.telegramUsername ?? ""}
                  onChange={(event) =>
                    setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, telegramUsername: event.target.value } }))
                  }
                  placeholder="@username"
                />
                <input
                  value={draft.discordUserId ?? ""}
                  onChange={(event) =>
                    setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, discordUserId: event.target.value } }))
                  }
                  placeholder="User ID"
                />
                <input
                  value={draft.discordUsername ?? ""}
                  onChange={(event) =>
                    setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, discordUsername: event.target.value } }))
                  }
                  placeholder="username"
                />
                <span className="profile-readonly-cell" title={formatProfileTimeZoneTitle(profile)}>{formatProfileTimeZoneLabel(profile)}</span>
                <input
                  type="color"
                  value={draft.authorColor ?? "#13a37b"}
                  onChange={(event) => setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, authorColor: event.target.value } }))}
                />
                <label className="checkbox-cell">
                  <input
                    type="checkbox"
                    checked={draft.pluginEnabled ?? true}
                    onChange={(event) =>
                      setDrafts((items) => ({ ...items, [profile.rawAuthor]: { ...draft, pluginEnabled: event.target.checked } }))
                    }
                  />
                  Enabled
                </label>
                <div className="profile-actions">
                  <button
                    className={settingsSaveButtonClassName(saveStatus[profile.rawAuthor], true)}
                    onClick={() => void saveProfile(profile.rawAuthor)}
                    disabled={saving === profile.rawAuthor || !profileDirty}
                  >
                    {settingsSaveButtonLabel(profile.rawAuthor, saving, saveStatus)}
                  </button>
                  <button
                    className={`${settingsSaveButtonClassName(saveStatus[deleteKey], true)} danger-button`}
                    onClick={() => setDeleteTarget(profile)}
                    disabled={saving === deleteKey}
                  >
                    {saving === deleteKey ? "Deleting..." : saveStatus[deleteKey] === "error" ? "Failed" : "Delete data"}
                  </button>
                  <button
                    className={`${settingsSaveButtonClassName(saveStatus[deleteProfileKey], true)} danger-button`}
                    onClick={() => setDeleteProfileTarget(profile)}
                    disabled={saving === deleteProfileKey}
                  >
                    {saving === deleteProfileKey ? "Deleting..." : saveStatus[deleteProfileKey] === "error" ? "Failed" : "Delete profile"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {deleteTarget ? (
        <AuthorDeleteConfirm
          profile={deleteTarget}
          saving={saving === `delete:${deleteTarget.rawAuthor}`}
          onCancel={() => setDeleteTarget(null)}
          onDelete={() => void deleteAuthorData(deleteTarget.rawAuthor)}
        />
      ) : null}
      {deleteProfileTarget ? (
        <AuthorProfileDeleteConfirm
          profile={deleteProfileTarget}
          saving={saving === `delete-profile:${deleteProfileTarget.rawAuthor}`}
          onCancel={() => setDeleteProfileTarget(null)}
          onDelete={() => void deleteAuthorProfile(deleteProfileTarget.rawAuthor)}
        />
      ) : null}
        </>
      ) : settingsTab === "discord" ? (
        <div className="panel">
          <h2>Discord</h2>
          <p className="settings-caption">
            Configure meeting channel automation. The Discord bot refreshes this value from the backend while it is running.
          </p>
          <div className="settings-row">
            <label>
              Auto-AFK timeout, sec
              <input
                value={discordAutoAfkTimeout}
                onChange={(event) => setDiscordAutoAfkTimeout(event.target.value)}
                type="number"
                min="60"
                step="30"
              />
            </label>
            <button className={settingsSaveButtonClassName(saveStatus.discord)} onClick={() => void saveDiscordSettings()} disabled={saving === "discord" || !discordSettingsDirty}>
              {settingsSaveButtonLabel("discord", saving, saveStatus)}
            </button>
          </div>
        </div>
      ) : settingsTab === "telegram" ? (
        <div className="panel">
          <h2>Telegram</h2>
          <p className="settings-caption">
            Minutes to wait after the first plugin activity on a day before the bot asks in the work chat whether you are online or whether activity was a mistake (requires Telegram username on the profile).
          </p>
          <div className="settings-row">
            <label>
              Online confirmation delay, minutes
              <input
                value={telegramOnlinePromptDelayMinutes}
                onChange={(event) => setTelegramOnlinePromptDelayMinutes(event.target.value)}
                type="number"
                min="1"
                max="1440"
                step="1"
              />
            </label>
            <button
              className={settingsSaveButtonClassName(saveStatus.telegramPrompt)}
              onClick={() => void saveTelegramPromptSettings()}
              disabled={saving === "telegramPrompt" || !telegramPromptSettingsDirty}
            >
              {settingsSaveButtonLabel("telegramPrompt", saving, saveStatus)}
            </button>
          </div>
        </div>
      ) : settingsTab === "meetingSummaries" ? (
        <>
          <div className="panel">
            <h2>Meeting Summaries</h2>
            <p className="settings-caption">
              Configure automatic Discord meeting summaries sent to the work Telegram chat.
            </p>
            <div className="settings-row meeting-summary-settings-row">
              <label>
                Meeting summaries
                <span className="checkbox-cell">
                  <input
                    type="checkbox"
                    checked={meetingSummariesEnabled}
                    onChange={(event) => setMeetingSummariesEnabled(event.target.checked)}
                  />
                  Enabled
                </span>
              </label>
              <label>
                Min participants
                <input
                  value={meetingSummaryMinParticipants}
                  onChange={(event) => setMeetingSummaryMinParticipants(event.target.value)}
                  type="number"
                  min="1"
                />
              </label>
              <label>
                Min duration, sec
                <input
                  value={meetingSummaryMinDuration}
                  onChange={(event) => setMeetingSummaryMinDuration(event.target.value)}
                  type="number"
                  min="1"
                  step="30"
                />
              </label>
              <label>
                Summary language
                <select value={meetingSummaryLanguage} onChange={(event) => setMeetingSummaryLanguage(event.target.value)}>
                  {MEETING_SUMMARY_LANGUAGES.map((language) => (
                    <option value={language} key={language}>{language}</option>
                  ))}
                </select>
              </label>
              <label>
                Send summaries to
                <select value={meetingSummaryRecipient} onChange={(event) => setMeetingSummaryRecipient(event.target.value)}>
                  <option value="work_chat">Work chat</option>
                  {profiles
                    .filter((profile) => profile.telegramUsername)
                    .map((profile) => (
                      <option value={profile.rawAuthor} key={profile.rawAuthor}>
                        {profile.displayName || profile.rawAuthor}
                        {profile.telegramPrivateChatId ? "" : " (send /start first)"}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Keep audio on server
                <select value={meetingAudioRetention} onChange={(event) => setMeetingAudioRetention(event.target.value)}>
                  {MEETING_AUDIO_RETENTION_OPTIONS.map((option) => (
                    <option value={String(option.value)} key={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
            </div>
          </div>
          <div className="meeting-summary-workspace">
            <div className="panel">
              <h3>Recent meeting summary activity</h3>
              <p className="settings-caption">
                Live status for the Discord recording, OpenAI summary, and Telegram delivery pipeline.
              </p>
              {meetingRecordingsError ? (
                <p className="empty">{meetingRecordingsError}</p>
              ) : meetingRecordings.length ? (
                <div className="settings-list">
                  {meetingRecordings.map((recording) => (
                    <div className="settings-list-item" key={recording.recordingId}>
                      <strong>{meetingRecordingStatusLabel(recording)}</strong>
                      <span>{meetingRecordingDetail(recording)}</span>
                      {recording.error ? <span className="alert-text">{recording.error}</span> : null}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="empty">No meeting summary activity yet.</p>
              )}
            </div>
            <div className="panel meeting-summary-prompt-panel">
              <h3>Summary instructions</h3>
              <p className="settings-caption">
                Prompt text used before the backend adds participants, required sections, language, and transcript automatically.
              </p>
              <label className="meeting-summary-prompt-field">
                Prompt
                <textarea
                  value={meetingSummaryPrompt}
                  onChange={(event) => setMeetingSummaryPrompt(event.target.value)}
                  rows={12}
                  placeholder="Instructions for turning a meeting transcript into a Telegram summary. The backend adds participants, required sections, language, and transcript automatically."
                />
              </label>
              <button
                className={settingsSaveButtonClassName(saveStatus.discord)}
                onClick={() => void saveDiscordSettings()}
                disabled={saving === "discord" || !meetingSummarySettingsDirty}
              >
                {settingsSaveButtonLabel("discord", saving, saveStatus)}
              </button>
            </div>
          </div>
        </>
      ) : (
        <SiteUsersPanel currentUser={currentUser} />
      )}
    </section>
  );
}

function intervalSettingsIdleThreshold(summary: Summary | null) {
  const intervalSettings = summary?.intervalSettings as (Summary["intervalSettings"] & { idleThresholdSeconds?: number }) | undefined;
  return intervalSettings?.idleThresholdSeconds ?? 300;
}

function intervalSettingsTelegramOnlinePromptMinutes(summary: Summary | null) {
  const intervalSettings = summary?.intervalSettings as (Summary["intervalSettings"] & { telegramOnlinePromptDelayMinutes?: number }) | undefined;
  return intervalSettings?.telegramOnlinePromptDelayMinutes ?? 15;
}

