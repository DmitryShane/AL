import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client";
import { SETTINGS_TAB_STORAGE_KEY } from "../constants/dashboard";
import type { AuthorProfile, MeetingActivityItem, OpenAIStats, SettingsTab, SiteUser, Summary } from "../types/dashboard";
import {
  authorProfilePayload,
  emptyAuthorProfile,
  loadSavedSettingsTab,
  normalizeAuthorInput,
  profileLocalTodayIso,
  type BulkActivityDeletePreset
} from "./pageHelpers";
import { SiteUsersPanel } from "../components/settings/SettingsComponents";
import { AuthorProfilesTab } from "../components/settings/tabs/authors/AuthorProfilesTab";
import { AutoBreakTab } from "../components/settings/tabs/autoBreak/AutoBreakTab";
import { SETTINGS_TABS } from "../components/settings/settingsTabs";
import { DeviceProfilesTab } from "../components/settings/tabs/deviceProfiles/DeviceProfilesTab";
import { DiscordSettingsTab } from "../components/settings/tabs/discord/DiscordSettingsTab";
import { GeneralSettingsTab } from "../components/settings/tabs/general/GeneralSettingsTab";
import { MeetingSummariesTab } from "../components/settings/tabs/meetingSummaries/MeetingSummariesTab";
import { AuthorRedirectsTab } from "../components/settings/tabs/redirects/AuthorRedirectsTab";
import { TelegramSettingsTab } from "../components/settings/tabs/telegram/TelegramSettingsTab";

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

const OPENAI_STATS_CACHE_KEY = "al.openAIStats.cache";
let cachedOpenAIStats: OpenAIStats | null = readCachedOpenAIStats();

async function apiErrorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    const detail = payload.detail;

    if (typeof detail === "string") {
      return detail;
    }

    if (Array.isArray(detail) && detail[0] && typeof detail[0] === "object" && detail[0] !== null && "msg" in detail[0]) {
      return String((detail[0] as { msg: string }).msg);
    }
  } catch {
    //
  }

  return `${fallback} (HTTP ${response.status})`;
}

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
  const canManageSettings = currentUser.role === "admin" || currentUser.role === "editor";
  const settingsReadOnly = !canManageSettings;
  const avatarSettingsLockedTitle = "Only editors and admins can change GitHub avatar cache settings.";
  const [settingsTab, setSettingsTabState] = useState<SettingsTab>(() => loadSavedSettingsTab());
  const [drafts, setDrafts] = useState<Record<string, AuthorProfile>>({});
  const [globalInterval, setGlobalInterval] = useState(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
  const [idleThreshold, setIdleThreshold] = useState(String(intervalSettingsIdleThreshold(summary)));
  const [deviceIdleThreshold, setDeviceIdleThreshold] = useState(String(intervalSettingsDeviceIdleThreshold(summary)));
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
  const [meetingSummaryTelegramTemplate, setMeetingSummaryTelegramTemplate] = useState(summary?.discordSettings.meetingSummaryTelegramTemplate ?? "");
  const [avatarRefreshCadence, setAvatarRefreshCadence] = useState<"week" | "month">(
    summary?.intervalSettings.avatarRefreshCadence === "week" ? "week" : "month"
  );
  const [saving, setSaving] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, "saved" | "error" | undefined>>({});
  const [aliasError, setAliasError] = useState("");
  const [deleteActivityDrafts, setDeleteActivityDrafts] = useState<Record<string, DeleteActivityDraft>>({});
  const [pendingAuthorActivityDelete, setPendingAuthorActivityDelete] = useState<PendingAuthorActivityDelete | null>(null);
  const [pendingAuthorActivityRebuild, setPendingAuthorActivityRebuild] = useState<PendingAuthorActivityRebuild | null>(null);
  const [deleteActivityFieldError, setDeleteActivityFieldError] = useState<Record<string, string>>({});
  const [deleteProfileTarget, setDeleteProfileTarget] = useState<AuthorProfile | null>(null);
  const [bulkActivityDeletePreset, setBulkActivityDeletePreset] = useState<BulkActivityDeletePreset>("1d");
  const [bulkActivityDeleteModalOpen, setBulkActivityDeleteModalOpen] = useState(false);
  const [fullActivityRebuildModalOpen, setFullActivityRebuildModalOpen] = useState(false);
  const [activityRebuildProgress, setActivityRebuildProgress] = useState<ActivityRebuildProgress | null>(null);
  const [newProfile, setNewProfile] = useState<AuthorProfile>(() => emptyAuthorProfile());
  const [aliasSource, setAliasSource] = useState("");
  const [aliasTarget, setAliasTarget] = useState("");
  const [meetingActivityItems, setMeetingActivityItems] = useState<MeetingActivityItem[]>([]);
  const [meetingRecordingsError, setMeetingRecordingsError] = useState("");
  const [openAIStats, setOpenAIStats] = useState<OpenAIStats | null>(() => cachedOpenAIStats);
  const [openAIStatsError, setOpenAIStatsError] = useState("");
  const [openAIStatsLoading, setOpenAIStatsLoading] = useState(() => cachedOpenAIStats === null);
  const [openAIStatsRefreshMode, setOpenAIStatsRefreshMode] = useState<"month" | "totals" | null>(null);
  const openAIStatsAutoLoadStartedRef = useRef(cachedOpenAIStats !== null);
  const meetingSummaryWorkspaceRef = useRef<HTMLDivElement>(null);
  const meetingSummaryPromptPanelRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (settingsTab !== "meetingSummaries") {
      return;
    }

    function syncPromptPanelHeight() {
      const workspaceEl = meetingSummaryWorkspaceRef.current;
      const promptPanelEl = meetingSummaryPromptPanelRef.current;

      if (!workspaceEl || !promptPanelEl) {
        return;
      }

      const height = promptPanelEl.getBoundingClientRect().height;
      workspaceEl.style.setProperty("--meeting-summary-prompt-panel-height", `${Math.round(height)}px`);
    }

    const workspaceEl = meetingSummaryWorkspaceRef.current;
    const promptPanelEl = meetingSummaryPromptPanelRef.current;

    if (!workspaceEl || !promptPanelEl) {
      return;
    }

    syncPromptPanelHeight();

    const observer = new ResizeObserver(syncPromptPanelHeight);
    observer.observe(promptPanelEl);

    return () => {
      observer.disconnect();
      meetingSummaryWorkspaceRef.current?.style.removeProperty("--meeting-summary-prompt-panel-height");
    };
  }, [settingsTab]);

  useEffect(() => {
    if (!summary) {
      return;
    }

    const nextDrafts: Record<string, AuthorProfile> = {};

    for (const profile of profiles) {
      nextDrafts[profile.rawAuthor] = { ...profile };
    }

    setDrafts(nextDrafts);
    setGlobalInterval(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
    setIdleThreshold(String(intervalSettingsIdleThreshold(summary)));
    setDeviceIdleThreshold(String(intervalSettingsDeviceIdleThreshold(summary)));
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
    setMeetingSummaryTelegramTemplate(summary?.discordSettings.meetingSummaryTelegramTemplate ?? "");
    setAvatarRefreshCadence(summary?.intervalSettings.avatarRefreshCadence === "week" ? "week" : "month");
  }, [summary]);

  useEffect(() => {
    if (!aliasTarget && profiles.length) {
      setAliasTarget(profiles[0].rawAuthor);
    }
  }, [aliasTarget, profiles]);

  useEffect(() => {
    setDeleteActivityDrafts((prev) => {
      const next = { ...prev };

      for (const profile of profiles) {
        if (!next[profile.rawAuthor]) {
          next[profile.rawAuthor] = { mode: "today", rangeStart: "", rangeEnd: "" };
        }
      }

      return next;
    });
  }, [profiles]);

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

        const data = await response.json() as { items?: MeetingActivityItem[] };

        if (!cancelled) {
          setMeetingActivityItems(data.items ?? []);
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

  useEffect(() => {
    if (!activityRebuildProgress?.jobId || activityRebuildProgress.status !== "running") {
      return;
    }

    let cancelled = false;
    const jobId = activityRebuildProgress.jobId;

    async function pollRebuildProgress() {
      try {
        const response = await apiFetch(`/api/v1/authors/activity/rebuild/status?${new URLSearchParams({ jobId }).toString()}`);

        if (!response.ok) {
          throw new Error(await apiErrorDetail(response, "Activity rebuild status load failed"));
        }

        const data = await response.json() as { job?: Partial<ActivityRebuildProgress> | null };
        const job = data.job;

        if (!cancelled && job?.jobId) {
          const status = job.status === "completed" || job.status === "failed" ? job.status : "running";
          setActivityRebuildProgress({
            jobId: String(job.jobId),
            label: String(job.label || "Activity rebuild"),
            status,
            phase: String(job.phase || (status === "completed" ? "Completed" : "Running")),
            progress: Number(job.progress ?? (status === "completed" ? 100 : 1)),
            error: typeof job.error === "string" ? job.error : undefined,
          });

          if (status === "completed") {
            onSaved();
          }
        }
      } catch (error) {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "Activity rebuild status load failed";
          setActivityRebuildProgress((current) => current ? { ...current, status: "failed", phase: "Status polling failed", error: message } : current);
        }
      }
    }

    void pollRebuildProgress();
    const intervalId = window.setInterval(() => void pollRebuildProgress(), 1000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activityRebuildProgress?.jobId, activityRebuildProgress?.status, onSaved]);

  useEffect(() => {
    if (settingsTab !== "meetingSummaries") {
      return;
    }

    if (openAIStatsAutoLoadStartedRef.current) {
      return;
    }

    openAIStatsAutoLoadStartedRef.current = true;
    void loadOpenAIStats();
  }, [settingsTab]);

  useEffect(() => {
    if (settingsTab !== "meetingSummaries") {
      return;
    }

    if (openAIStats?.syncStatus !== "syncingTotals" && openAIStats?.syncStatus !== "syncingMonth") {
      return;
    }

    const timer = window.setTimeout(() => {
      void loadOpenAIStats();
    }, 5000);

    return () => window.clearTimeout(timer);
  }, [openAIStats?.syncStatus, openAIStats?.syncProgressCurrent, settingsTab]);

  function setSettingsTab(tab: SettingsTab) {
    setSettingsTabState(tab);
    localStorage.setItem(SETTINGS_TAB_STORAGE_KEY, tab);
  }

  async function loadOpenAIStats(refresh: "month" | "totals" | null = null) {
    if (refresh) {
      setOpenAIStatsRefreshMode(refresh);
    } else {
      setOpenAIStatsLoading(true);
    }

    try {
      const response = await apiFetch(`/api/v1/settings/openai-stats${refresh ? `?refresh=${refresh}` : ""}`);

      if (!response.ok) {
        throw new Error(await apiErrorDetail(response, "OpenAI stats load failed"));
      }

      const data = await response.json() as OpenAIStats;
      cachedOpenAIStats = data;
      writeCachedOpenAIStats(data);
      setOpenAIStats(data);
      setOpenAIStatsError(data.error || "");
    } catch (error) {
      setOpenAIStatsError(error instanceof Error ? error.message : "OpenAI stats load failed");
    } finally {
      if (refresh) {
        setOpenAIStatsRefreshMode(null);
      } else {
        setOpenAIStatsLoading(false);
      }
    }
  }

  const meetingSummarySettingsDirty =
    meetingSummariesEnabled !== Boolean(summary?.discordSettings.meetingSummariesEnabled) ||
    meetingSummaryMinParticipants !== String(summary?.discordSettings.meetingSummaryMinParticipants ?? 2) ||
    meetingSummaryMinDuration !== String(summary?.discordSettings.meetingSummaryMinDurationSeconds ?? 120) ||
    meetingSummaryLanguage !== (summary?.discordSettings.meetingSummaryLanguage ?? "English") ||
    meetingSummaryRecipient !== (summary?.discordSettings.meetingSummaryRecipient ?? "work_chat") ||
    meetingAudioRetention !== String(summary?.discordSettings.meetingAudioRetentionSeconds ?? 0);
  const meetingSummaryPromptDirty = meetingSummaryPrompt !== (summary?.discordSettings.meetingSummaryPrompt ?? "");
  const meetingSummaryTelegramTemplateDirty = meetingSummaryTelegramTemplate !== (summary?.discordSettings.meetingSummaryTelegramTemplate ?? "");
  const discordSettingsDirty = discordAutoAfkTimeout !== String(summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600);
  const telegramPromptSettingsDirty =
    telegramOnlinePromptDelayMinutes !== String(intervalSettingsTelegramOnlinePromptMinutes(summary));

  async function saveProfile(rawAuthor: string) {
    if (settingsReadOnly) {
      return;
    }

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
    if (settingsReadOnly) {
      return;
    }

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

  async function saveAvatarRefreshCadence() {
    if (settingsReadOnly) {
      return;
    }

    setSaving("avatarCadence");
    setSaveStatus((items) => ({ ...items, avatarCadence: undefined }));

    try {
      const response = await apiFetch("/api/v1/settings/avatars", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refreshCadence: avatarRefreshCadence })
      });

      if (!response.ok) {
        throw new Error(await apiErrorDetail(response, "Avatar cadence save failed"));
      }

      setSaveStatus((items) => ({ ...items, avatarCadence: "saved" }));
      onSaved();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Avatar cadence save failed";
      window.alert(message);
      setSaveStatus((items) => ({ ...items, avatarCadence: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, avatarCadence: undefined }));
      }, 2500);
    }
  }

  async function refreshAllGitHubAvatars() {
    if (settingsReadOnly) {
      return;
    }

    setSaving("avatar-refresh-all");
    setSaveStatus((items) => ({ ...items, avatarRefreshAll: undefined }));

    try {
      const response = await apiFetch("/api/v1/authors/avatars/refresh-all", { method: "POST" });

      if (!response.ok) {
        throw new Error(await apiErrorDetail(response, "Refresh all avatars failed"));
      }

      setSaveStatus((items) => ({ ...items, avatarRefreshAll: "saved" }));
      onSaved();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Refresh all avatars failed";
      window.alert(message);
      setSaveStatus((items) => ({ ...items, avatarRefreshAll: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, avatarRefreshAll: undefined }));
      }, 2500);
    }
  }

  async function refreshAuthorGitHubAvatar(rawAuthor: string) {
    if (settingsReadOnly) {
      return;
    }

    const key = `avatar-refresh:${rawAuthor}`;
    setSaving(key);
    setSaveStatus((items) => ({ ...items, [key]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/authors/${encodeURIComponent(rawAuthor)}/avatar/refresh`, {
        method: "POST"
      });

      if (!response.ok) {
        throw new Error(await apiErrorDetail(response, "Avatar refresh failed"));
      }

      setSaveStatus((items) => ({ ...items, [key]: "saved" }));
      onSaved();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Avatar refresh failed";
      window.alert(message);
      setSaveStatus((items) => ({ ...items, [key]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [key]: undefined }));
      }, 2500);
    }
  }

  async function saveInterval() {
    if (settingsReadOnly) {
      return;
    }

    setSaving("interval");
    setSaveStatus((items) => ({ ...items, interval: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/intervals`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          defaultSendIntervalSeconds: Number(globalInterval),
          idleThresholdSeconds: Number(idleThreshold),
          deviceIdleThresholdSeconds: Number(deviceIdleThreshold),
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
    if (settingsReadOnly) {
      return;
    }

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
    if (settingsReadOnly) {
      return;
    }

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
          meetingSummaryPrompt,
          meetingSummaryTelegramTemplate
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

  async function saveMeetingSummarySettings() {
    if (settingsReadOnly) {
      return;
    }

    setSaving("meetingSummarySettings");
    setSaveStatus((items) => ({ ...items, meetingSummarySettings: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/discord`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meetingAutoAfkTimeoutSeconds: summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? Number(discordAutoAfkTimeout),
          meetingSummariesEnabled,
          meetingSummaryMinParticipants: Number(meetingSummaryMinParticipants),
          meetingSummaryMinDurationSeconds: Number(meetingSummaryMinDuration),
          meetingSummaryLanguage,
          meetingSummaryRecipient,
          meetingAudioRetentionSeconds: Number(meetingAudioRetention),
          meetingSummaryPrompt: summary?.discordSettings.meetingSummaryPrompt ?? meetingSummaryPrompt,
          meetingSummaryTelegramTemplate: summary?.discordSettings.meetingSummaryTelegramTemplate ?? meetingSummaryTelegramTemplate
        })
      });

      if (!response.ok) {
        throw new Error("Meeting summary settings save failed");
      }

      setSaveStatus((items) => ({ ...items, meetingSummarySettings: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, meetingSummarySettings: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, meetingSummarySettings: undefined }));
      }, 2500);
    }
  }

  async function saveMeetingSummaryPrompt() {
    if (settingsReadOnly) {
      return;
    }

    setSaving("meetingSummaryPrompt");
    setSaveStatus((items) => ({ ...items, meetingSummaryPrompt: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/discord`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meetingAutoAfkTimeoutSeconds: summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? Number(discordAutoAfkTimeout),
          meetingSummariesEnabled: Boolean(summary?.discordSettings.meetingSummariesEnabled),
          meetingSummaryMinParticipants: summary?.discordSettings.meetingSummaryMinParticipants ?? Number(meetingSummaryMinParticipants),
          meetingSummaryMinDurationSeconds: summary?.discordSettings.meetingSummaryMinDurationSeconds ?? Number(meetingSummaryMinDuration),
          meetingSummaryLanguage: summary?.discordSettings.meetingSummaryLanguage ?? meetingSummaryLanguage,
          meetingSummaryRecipient: summary?.discordSettings.meetingSummaryRecipient ?? meetingSummaryRecipient,
          meetingAudioRetentionSeconds: summary?.discordSettings.meetingAudioRetentionSeconds ?? Number(meetingAudioRetention),
          meetingSummaryPrompt,
          meetingSummaryTelegramTemplate: summary?.discordSettings.meetingSummaryTelegramTemplate ?? meetingSummaryTelegramTemplate
        })
      });

      if (!response.ok) {
        throw new Error("Meeting summary prompt save failed");
      }

      setSaveStatus((items) => ({ ...items, meetingSummaryPrompt: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, meetingSummaryPrompt: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, meetingSummaryPrompt: undefined }));
      }, 2500);
    }
  }

  async function saveMeetingSummaryTelegramTemplate() {
    if (settingsReadOnly) {
      return;
    }

    setSaving("meetingSummaryTelegramTemplate");
    setSaveStatus((items) => ({ ...items, meetingSummaryTelegramTemplate: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/discord`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meetingAutoAfkTimeoutSeconds: summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? Number(discordAutoAfkTimeout),
          meetingSummariesEnabled: Boolean(summary?.discordSettings.meetingSummariesEnabled),
          meetingSummaryMinParticipants: summary?.discordSettings.meetingSummaryMinParticipants ?? Number(meetingSummaryMinParticipants),
          meetingSummaryMinDurationSeconds: summary?.discordSettings.meetingSummaryMinDurationSeconds ?? Number(meetingSummaryMinDuration),
          meetingSummaryLanguage: summary?.discordSettings.meetingSummaryLanguage ?? meetingSummaryLanguage,
          meetingSummaryRecipient: summary?.discordSettings.meetingSummaryRecipient ?? meetingSummaryRecipient,
          meetingAudioRetentionSeconds: summary?.discordSettings.meetingAudioRetentionSeconds ?? Number(meetingAudioRetention),
          meetingSummaryPrompt: summary?.discordSettings.meetingSummaryPrompt ?? meetingSummaryPrompt,
          meetingSummaryTelegramTemplate
        })
      });

      if (!response.ok) {
        throw new Error("Meeting summary Telegram template save failed");
      }

      setSaveStatus((items) => ({ ...items, meetingSummaryTelegramTemplate: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, meetingSummaryTelegramTemplate: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, meetingSummaryTelegramTemplate: undefined }));
      }, 2500);
    }
  }

  async function executeAuthorActivityDelete(pending: PendingAuthorActivityDelete) {
    if (settingsReadOnly) {
      return;
    }

    const rawAuthor = pending.profile.rawAuthor;
    const deleteKey = `delete:${rawAuthor}`;
    setSaving(deleteKey);
    setSaveStatus((items) => ({ ...items, [deleteKey]: undefined }));

    try {
      let url = `/api/v1/authors/${encodeURIComponent(rawAuthor)}/data`;

      if (pending.mode === "range") {
        const params = new URLSearchParams({
          startDate: pending.startDate,
          endDate: pending.endDate
        });
        url += `?${params.toString()}`;
      }

      const response = await apiFetch(url, {
        method: "DELETE"
      });

      if (!response.ok) {
        throw new Error("Author data delete failed");
      }

      setPendingAuthorActivityDelete(null);
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

  async function executeBulkActivityDeleteAllAuthors(confirmPhrase: string) {
    if (settingsReadOnly) {
      return;
    }

    setSaving("bulk-delete-all-authors");
    setSaveStatus((items) => ({ ...items, bulkDeleteAllAuthors: undefined }));

    try {
      const response = await apiFetch("/api/v1/authors/activity/bulk-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preset: bulkActivityDeletePreset,
          confirmPhrase
        })
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(String(payload.detail || "Bulk delete failed"));
      }

      const data = await response.json().catch(() => ({}));

      if (data.failures?.length) {
        throw new Error(`Some authors failed: ${JSON.stringify(data.failures)}`);
      }

      setBulkActivityDeleteModalOpen(false);
      setSaveStatus((items) => ({ ...items, bulkDeleteAllAuthors: "saved" }));
      onSaved();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Bulk delete failed";
      window.alert(message);
      setSaveStatus((items) => ({ ...items, bulkDeleteAllAuthors: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, bulkDeleteAllAuthors: undefined }));
      }, 2500);
    }
  }

  async function executeFullActivityRebuild(confirmPhrase: string) {
    if (settingsReadOnly) {
      return;
    }

    setSaving("full-activity-rebuild");
    setSaveStatus((items) => ({ ...items, fullActivityRebuild: undefined }));

    try {
      const response = await apiFetch("/api/v1/authors/activity/rebuild", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmPhrase })
      });

      if (!response.ok) {
        throw new Error(await apiErrorDetail(response, "Full activity rebuild failed"));
      }

      const data = await response.json() as { jobId?: string };

      if (!data.jobId) {
        throw new Error("Full activity rebuild did not return a job id");
      }

      setActivityRebuildProgress({
        jobId: data.jobId,
        label: "Rebuild full DB",
        status: "running",
        phase: "Queued",
        progress: 1,
      });
      setFullActivityRebuildModalOpen(false);
      setSaveStatus((items) => ({ ...items, fullActivityRebuild: "saved" }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Full activity rebuild failed";
      window.alert(message);
      setSaveStatus((items) => ({ ...items, fullActivityRebuild: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, fullActivityRebuild: undefined }));
      }, 2500);
    }
  }

  async function executeAuthorActivityRebuild(pending: PendingAuthorActivityRebuild) {
    if (settingsReadOnly) {
      return;
    }

    const rawAuthor = pending.profile.rawAuthor;
    const rebuildKey = `rebuild:${rawAuthor}`;
    setSaving(rebuildKey);
    setSaveStatus((items) => ({ ...items, [rebuildKey]: undefined }));

    try {
      const params = new URLSearchParams({
        startDate: pending.startDate,
        endDate: pending.endDate
      });
      const response = await apiFetch(`/api/v1/authors/${encodeURIComponent(rawAuthor)}/activity/rebuild?${params.toString()}`, {
        method: "POST"
      });

      if (!response.ok) {
        throw new Error(await apiErrorDetail(response, "Author activity rebuild failed"));
      }

      const data = await response.json() as { jobId?: string };

      if (!data.jobId) {
        throw new Error("Author activity rebuild did not return a job id");
      }

      setActivityRebuildProgress({
        jobId: data.jobId,
        label: `Rebuild ${rawAuthor}`,
        status: "running",
        phase: "Queued",
        progress: 1,
      });
      setPendingAuthorActivityRebuild(null);
      setSaveStatus((items) => ({ ...items, [rebuildKey]: "saved" }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Author activity rebuild failed";
      window.alert(message);
      setSaveStatus((items) => ({ ...items, [rebuildKey]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, [rebuildKey]: undefined }));
      }, 2500);
    }
  }

  function requestAuthorActivityDelete(profile: AuthorProfile) {
    if (settingsReadOnly) {
      return;
    }

    const draft: DeleteActivityDraft =
      deleteActivityDrafts[profile.rawAuthor] ?? { mode: "today", rangeStart: "", rangeEnd: "" };

    setDeleteActivityFieldError((items) => {
      const next = { ...items };
      delete next[profile.rawAuthor];
      return next;
    });

    if (draft.mode === "today") {
      const day = profileLocalTodayIso(profile);
      setPendingAuthorActivityDelete({ mode: "range", profile, startDate: day, endDate: day });
      return;
    }

    if (!draft.rangeStart.trim() || !draft.rangeEnd.trim()) {
      setDeleteActivityFieldError((items) => ({
        ...items,
        [profile.rawAuthor]: "Choose both start and end dates."
      }));
      return;
    }

    if (draft.rangeStart > draft.rangeEnd) {
      setDeleteActivityFieldError((items) => ({
        ...items,
        [profile.rawAuthor]: "Start date must be on or before end date."
      }));
      return;
    }

    setPendingAuthorActivityDelete({
      mode: "range",
      profile,
      startDate: draft.rangeStart,
      endDate: draft.rangeEnd
    });
  }

  function resolveAuthorActivityPeriod(profile: AuthorProfile): { startDate: string; endDate: string } | null {
    const draft: DeleteActivityDraft =
      deleteActivityDrafts[profile.rawAuthor] ?? { mode: "today", rangeStart: "", rangeEnd: "" };

    setDeleteActivityFieldError((items) => {
      const next = { ...items };
      delete next[profile.rawAuthor];
      return next;
    });

    if (draft.mode === "today") {
      const day = profileLocalTodayIso(profile);
      return { startDate: day, endDate: day };
    }

    if (!draft.rangeStart.trim() || !draft.rangeEnd.trim()) {
      setDeleteActivityFieldError((items) => ({
        ...items,
        [profile.rawAuthor]: "Choose both start and end dates."
      }));
      return null;
    }

    if (draft.rangeStart > draft.rangeEnd) {
      setDeleteActivityFieldError((items) => ({
        ...items,
        [profile.rawAuthor]: "Start date must be on or before end date."
      }));
      return null;
    }

    return { startDate: draft.rangeStart, endDate: draft.rangeEnd };
  }

  function requestAuthorActivityRebuild(profile: AuthorProfile) {
    if (settingsReadOnly) {
      return;
    }

    const period = resolveAuthorActivityPeriod(profile);

    if (!period) {
      return;
    }

    setPendingAuthorActivityRebuild({
      profile,
      startDate: period.startDate,
      endDate: period.endDate
    });
  }

  function requestAuthorDeleteAllActivity(profile: AuthorProfile) {
    if (settingsReadOnly) {
      return;
    }

    setDeleteActivityFieldError((items) => {
      const next = { ...items };
      delete next[profile.rawAuthor];
      return next;
    });
    setPendingAuthorActivityDelete({ mode: "all", profile });
  }

  async function deleteAuthorProfile(rawAuthor: string) {
    if (settingsReadOnly) {
      return;
    }

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
    if (settingsReadOnly) {
      return;
    }

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
    if (settingsReadOnly) {
      return;
    }

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
      (draft.githubUsername ?? "") !== (profile.githubUsername ?? "") ||
      (draft.pluginEnabled ?? true) !== (profile.pluginEnabled ?? true) ||
      (draft.autoBreakEnabled ?? false) !== (profile.autoBreakEnabled ?? false)
    );
  }

  const savedGlobalInterval = String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300);
  const savedIdleThreshold = String(intervalSettingsIdleThreshold(summary));
  const savedDeviceIdleThreshold = String(intervalSettingsDeviceIdleThreshold(summary));
  const savedPluginIngestEnabled = summary?.intervalSettings.pluginIngestEnabled ?? true;
  const isIntervalSettingsDirty =
    globalInterval !== savedGlobalInterval ||
    idleThreshold !== savedIdleThreshold ||
    deviceIdleThreshold !== savedDeviceIdleThreshold ||
    pluginIngestEnabled !== savedPluginIngestEnabled;
  const savedAvatarRefreshCadence: "week" | "month" =
    summary?.intervalSettings.avatarRefreshCadence === "week" ? "week" : "month";
  const isAvatarCadenceDirty = avatarRefreshCadence !== savedAvatarRefreshCadence;

  return (
    <section className="page-section settings-layout">
      <div className="settings-tabs">
        {SETTINGS_TABS.map((tab) => (
          <button key={tab.key} className={settingsTab === tab.key ? "active" : ""} onClick={() => setSettingsTab(tab.key)}>
            {tab.label}
          </button>
        ))}
      </div>
      {settingsTab === "general" ? (
        <GeneralSettingsTab
          intervalSettings={summary?.intervalSettings}
          globalInterval={globalInterval}
          idleThreshold={idleThreshold}
          deviceIdleThreshold={deviceIdleThreshold}
          pluginIngestEnabled={pluginIngestEnabled}
          settingsReadOnly={settingsReadOnly}
          saving={saving}
          saveStatus={saveStatus}
          isIntervalSettingsDirty={isIntervalSettingsDirty}
          onGlobalIntervalChange={setGlobalInterval}
          onIdleThresholdChange={setIdleThreshold}
          onDeviceIdleThresholdChange={setDeviceIdleThreshold}
          onPluginIngestEnabledChange={setPluginIngestEnabled}
          onSaveInterval={() => void saveInterval()}
        />
      ) : settingsTab === "autoBreak" ? (
        <AutoBreakTab
          profiles={profiles}
          drafts={drafts}
          settingsReadOnly={settingsReadOnly}
          saving={saving}
          saveStatus={saveStatus}
          isProfileDirty={isProfileDirty}
          onDraftChange={(rawAuthor, draft) => setDrafts((items) => ({ ...items, [rawAuthor]: draft }))}
          onSaveProfile={(rawAuthor) => void saveProfile(rawAuthor)}
        />
      ) : settingsTab === "deviceProfiles" ? (
        <DeviceProfilesTab />
      ) : settingsTab === "redirects" ? (
        <AuthorRedirectsTab
          profiles={profiles}
          aliases={aliases}
          aliasSource={aliasSource}
          aliasTarget={aliasTarget}
          aliasError={aliasError}
          settingsReadOnly={settingsReadOnly}
          saving={saving}
          saveStatus={saveStatus}
          onAliasSourceChange={setAliasSource}
          onAliasTargetChange={setAliasTarget}
          onSaveAuthorAlias={() => void saveAuthorAlias()}
          onDeleteAuthorAlias={(sourceRawAuthor) => void deleteAuthorAlias(sourceRawAuthor)}
        />
      ) : settingsTab === "authors" ? (
        <AuthorProfilesTab
          profiles={profiles}
          drafts={drafts}
          newProfile={newProfile}
          avatarRefreshCadence={avatarRefreshCadence}
          deleteActivityDrafts={deleteActivityDrafts}
          pendingAuthorActivityDelete={pendingAuthorActivityDelete}
          pendingAuthorActivityRebuild={pendingAuthorActivityRebuild}
          deleteActivityFieldError={deleteActivityFieldError}
          deleteProfileTarget={deleteProfileTarget}
          bulkActivityDeletePreset={bulkActivityDeletePreset}
          bulkActivityDeleteModalOpen={bulkActivityDeleteModalOpen}
          fullActivityRebuildModalOpen={fullActivityRebuildModalOpen}
          activityRebuildProgress={activityRebuildProgress}
          canManageSettings={canManageSettings}
          settingsReadOnly={settingsReadOnly}
          avatarSettingsLockedTitle={avatarSettingsLockedTitle}
          saving={saving}
          saveStatus={saveStatus}
          isAvatarCadenceDirty={isAvatarCadenceDirty}
          isProfileDirty={isProfileDirty}
          onDraftChange={(rawAuthor, draft) => setDrafts((items) => ({ ...items, [rawAuthor]: draft }))}
          onNewProfileChange={setNewProfile}
          onAvatarRefreshCadenceChange={setAvatarRefreshCadence}
          onDeleteActivityDraftChange={(rawAuthor, draft) => setDeleteActivityDrafts((items) => ({ ...items, [rawAuthor]: draft }))}
          onBulkActivityDeletePresetChange={setBulkActivityDeletePreset}
          onBulkActivityDeleteModalOpenChange={setBulkActivityDeleteModalOpen}
          onFullActivityRebuildModalOpenChange={setFullActivityRebuildModalOpen}
          onPendingAuthorActivityDeleteChange={setPendingAuthorActivityDelete}
          onPendingAuthorActivityRebuildChange={setPendingAuthorActivityRebuild}
          onDeleteProfileTargetChange={setDeleteProfileTarget}
          onCreateProfile={() => void createProfile()}
          onSaveProfile={(rawAuthor) => void saveProfile(rawAuthor)}
          onSaveAvatarRefreshCadence={() => void saveAvatarRefreshCadence()}
          onRefreshAllGitHubAvatars={() => void refreshAllGitHubAvatars()}
          onRefreshAuthorGitHubAvatar={(rawAuthor) => void refreshAuthorGitHubAvatar(rawAuthor)}
          onRequestAuthorActivityDelete={requestAuthorActivityDelete}
          onRequestAuthorActivityRebuild={requestAuthorActivityRebuild}
          onRequestAuthorDeleteAllActivity={requestAuthorDeleteAllActivity}
          onExecuteBulkActivityDeleteAllAuthors={(confirmPhrase) => void executeBulkActivityDeleteAllAuthors(confirmPhrase)}
          onExecuteFullActivityRebuild={(confirmPhrase) => void executeFullActivityRebuild(confirmPhrase)}
          onExecuteAuthorActivityDelete={(pending) => void executeAuthorActivityDelete(pending)}
          onExecuteAuthorActivityRebuild={(pending) => void executeAuthorActivityRebuild(pending)}
          onDeleteAuthorProfile={(rawAuthor) => void deleteAuthorProfile(rawAuthor)}
        />
      ) : settingsTab === "discord" ? (
        <DiscordSettingsTab
          discordAutoAfkTimeout={discordAutoAfkTimeout}
          settingsReadOnly={settingsReadOnly}
          saving={saving}
          saveStatus={saveStatus}
          discordSettingsDirty={discordSettingsDirty}
          onDiscordAutoAfkTimeoutChange={setDiscordAutoAfkTimeout}
          onSaveDiscordSettings={() => void saveDiscordSettings()}
        />
      ) : settingsTab === "telegram" ? (
        <TelegramSettingsTab
          telegramOnlinePromptDelayMinutes={telegramOnlinePromptDelayMinutes}
          settingsReadOnly={settingsReadOnly}
          saving={saving}
          saveStatus={saveStatus}
          telegramPromptSettingsDirty={telegramPromptSettingsDirty}
          onTelegramOnlinePromptDelayMinutesChange={setTelegramOnlinePromptDelayMinutes}
          onSaveTelegramPromptSettings={() => void saveTelegramPromptSettings()}
        />
      ) : settingsTab === "meetingSummaries" ? (
        <MeetingSummariesTab
          workspaceRef={meetingSummaryWorkspaceRef}
          promptPanelRef={meetingSummaryPromptPanelRef}
          profiles={profiles}
          meetingSummariesEnabled={meetingSummariesEnabled}
          meetingSummaryMinParticipants={meetingSummaryMinParticipants}
          meetingSummaryMinDuration={meetingSummaryMinDuration}
          meetingSummaryLanguage={meetingSummaryLanguage}
          meetingSummaryRecipient={meetingSummaryRecipient}
          meetingAudioRetention={meetingAudioRetention}
          meetingSummaryPrompt={meetingSummaryPrompt}
          meetingSummaryTelegramTemplate={meetingSummaryTelegramTemplate}
          meetingActivityItems={meetingActivityItems}
          meetingRecordingsError={meetingRecordingsError}
          openAIStats={openAIStats}
          openAIStatsError={openAIStatsError}
          openAIStatsLoading={openAIStatsLoading}
          openAIStatsRefreshMode={openAIStatsRefreshMode}
          settingsReadOnly={settingsReadOnly}
          saving={saving}
          saveStatus={saveStatus}
          meetingSummarySettingsDirty={meetingSummarySettingsDirty}
          meetingSummaryPromptDirty={meetingSummaryPromptDirty}
          meetingSummaryTelegramTemplateDirty={meetingSummaryTelegramTemplateDirty}
          onMeetingSummariesEnabledChange={setMeetingSummariesEnabled}
          onMeetingSummaryMinParticipantsChange={setMeetingSummaryMinParticipants}
          onMeetingSummaryMinDurationChange={setMeetingSummaryMinDuration}
          onMeetingSummaryLanguageChange={setMeetingSummaryLanguage}
          onMeetingSummaryRecipientChange={setMeetingSummaryRecipient}
          onMeetingAudioRetentionChange={setMeetingAudioRetention}
          onMeetingSummaryPromptChange={setMeetingSummaryPrompt}
          onMeetingSummaryTelegramTemplateChange={setMeetingSummaryTelegramTemplate}
          onSaveMeetingSummarySettings={() => void saveMeetingSummarySettings()}
          onSaveMeetingSummaryPrompt={() => void saveMeetingSummaryPrompt()}
          onSaveMeetingSummaryTelegramTemplate={() => void saveMeetingSummaryTelegramTemplate()}
          onRefreshOpenAIStats={() => void loadOpenAIStats("month")}
          onRefreshOpenAIStatsTotals={() => void loadOpenAIStats("totals")}
        />
      ) : (
        <SiteUsersPanel currentUser={currentUser} authorProfiles={profiles} authorProfileDrafts={drafts} />
      )}
    </section>
  );
}

function intervalSettingsIdleThreshold(summary: Summary | null) {
  const intervalSettings = summary?.intervalSettings as (Summary["intervalSettings"] & { idleThresholdSeconds?: number }) | undefined;
  return intervalSettings?.idleThresholdSeconds ?? 300;
}

function intervalSettingsDeviceIdleThreshold(summary: Summary | null) {
  const intervalSettings = summary?.intervalSettings as (Summary["intervalSettings"] & { deviceIdleThresholdSeconds?: number }) | undefined;
  return intervalSettings?.deviceIdleThresholdSeconds ?? 300;
}

function intervalSettingsTelegramOnlinePromptMinutes(summary: Summary | null) {
  const intervalSettings = summary?.intervalSettings as (Summary["intervalSettings"] & { telegramOnlinePromptDelayMinutes?: number }) | undefined;
  return intervalSettings?.telegramOnlinePromptDelayMinutes ?? 15;
}

function readCachedOpenAIStats(): OpenAIStats | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const value = window.sessionStorage.getItem(OPENAI_STATS_CACHE_KEY);

    if (!value) {
      return null;
    }

    return JSON.parse(value) as OpenAIStats;
  } catch {
    return null;
  }
}

function writeCachedOpenAIStats(stats: OpenAIStats): void {
  try {
    window.sessionStorage.setItem(OPENAI_STATS_CACHE_KEY, JSON.stringify(stats));
  } catch {
    // Storage can be unavailable in private mode; in-memory cache still keeps the card populated.
  }
}
