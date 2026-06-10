import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { apiFetch } from "../../../../api/client";
import { MEETING_AUDIO_RETENTION_OPTIONS, MEETING_SUMMARY_LANGUAGES } from "../../../../constants/dashboard";
import type { MeetingActivityItem, OpenAIStats, Summary } from "../../../../types/dashboard";
import { readStorageItem, sessionBrowserStorage, writeStorageCache } from "../../../../utils/browserStorage";
import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";
import { OpenAIStatsCard } from "./OpenAIStatsCard";
import { RecentMeetingSummaryActivityCard } from "./RecentMeetingSummaryActivityCard";
import { SummaryInstructionsCard } from "./SummaryInstructionsCard";
import { TelegramSummaryFormatCard } from "./TelegramSummaryFormatCard";

type MeetingSummariesTabProps = {
  summary: Summary | null;
  settingsReadOnly: boolean;
  onSaved: () => void;
};

export function MeetingSummariesTab({
  settingsReadOnly,
  summary,
  onSaved
}: MeetingSummariesTabProps) {
  const profiles = summary?.activitySummary.profiles ?? [];
  const workspaceRef = useRef<HTMLDivElement>(null);
  const promptPanelRef = useRef<HTMLDivElement>(null);
  const [meetingSummariesEnabled, setMeetingSummariesEnabled] = useState(Boolean(summary?.discordSettings.meetingSummariesEnabled));
  const [meetingSummaryMinParticipants, setMeetingSummaryMinParticipants] = useState(String(summary?.discordSettings.meetingSummaryMinParticipants ?? 2));
  const [meetingSummaryMinDuration, setMeetingSummaryMinDuration] = useState(String(summary?.discordSettings.meetingSummaryMinDurationSeconds ?? 120));
  const [meetingSummaryLanguage, setMeetingSummaryLanguage] = useState(summary?.discordSettings.meetingSummaryLanguage ?? "English");
  const [meetingSummaryRecipient, setMeetingSummaryRecipient] = useState(summary?.discordSettings.meetingSummaryRecipient ?? "work_chat");
  const [meetingAudioRetention, setMeetingAudioRetention] = useState(String(summary?.discordSettings.meetingAudioRetentionSeconds ?? 0));
  const [meetingSummaryPrompt, setMeetingSummaryPrompt] = useState(summary?.discordSettings.meetingSummaryPrompt ?? "");
  const [meetingSummaryTelegramTemplate, setMeetingSummaryTelegramTemplate] = useState(summary?.discordSettings.meetingSummaryTelegramTemplate ?? "");
  const [meetingActivityItems, setMeetingActivityItems] = useState<MeetingActivityItem[]>([]);
  const [meetingRecordingsError, setMeetingRecordingsError] = useState("");
  const [openAIStats, setOpenAIStats] = useState<OpenAIStats | null>(() => cachedOpenAIStats);
  const [openAIStatsError, setOpenAIStatsError] = useState("");
  const [openAIStatsLoading, setOpenAIStatsLoading] = useState(() => cachedOpenAIStats === null);
  const [openAIStatsRefreshMode, setOpenAIStatsRefreshMode] = useState<"month" | "totals" | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, "saved" | "error" | undefined>>({});
  const openAIStatsAutoLoadStartedRef = useRef(cachedOpenAIStats !== null);

  useLayoutEffect(() => {
    function syncPromptPanelHeight() {
      const workspaceEl = workspaceRef.current;
      const promptPanelEl = promptPanelRef.current;

      if (!workspaceEl || !promptPanelEl) {
        return;
      }

      const height = promptPanelEl.getBoundingClientRect().height;
      workspaceEl.style.setProperty("--meeting-summary-prompt-panel-height", `${Math.round(height)}px`);
    }

    const workspaceEl = workspaceRef.current;
    const promptPanelEl = promptPanelRef.current;

    if (!workspaceEl || !promptPanelEl) {
      return;
    }

    syncPromptPanelHeight();

    const observer = new ResizeObserver(syncPromptPanelHeight);
    observer.observe(promptPanelEl);

    return () => {
      observer.disconnect();
      workspaceRef.current?.style.removeProperty("--meeting-summary-prompt-panel-height");
    };
  }, []);

  useEffect(() => {
    if (!summary) {
      return;
    }

    setMeetingSummariesEnabled(Boolean(summary.discordSettings.meetingSummariesEnabled));
    setMeetingSummaryMinParticipants(String(summary.discordSettings.meetingSummaryMinParticipants ?? 2));
    setMeetingSummaryMinDuration(String(summary.discordSettings.meetingSummaryMinDurationSeconds ?? 120));
    setMeetingSummaryLanguage(summary.discordSettings.meetingSummaryLanguage ?? "English");
    setMeetingSummaryRecipient(summary.discordSettings.meetingSummaryRecipient ?? "work_chat");
    setMeetingAudioRetention(String(summary.discordSettings.meetingAudioRetentionSeconds ?? 0));
    setMeetingSummaryPrompt(summary.discordSettings.meetingSummaryPrompt ?? "");
    setMeetingSummaryTelegramTemplate(summary.discordSettings.meetingSummaryTelegramTemplate ?? "");
  }, [summary]);

  useEffect(() => {
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
  }, []);

  useEffect(() => {
    if (openAIStatsAutoLoadStartedRef.current) {
      return;
    }

    openAIStatsAutoLoadStartedRef.current = true;
    void loadOpenAIStats();
  }, []);

  useEffect(() => {
    if (openAIStats?.syncStatus !== "syncingTotals" && openAIStats?.syncStatus !== "syncingMonth") {
      return;
    }

    const timer = window.setTimeout(() => {
      void loadOpenAIStats();
    }, 5000);

    return () => window.clearTimeout(timer);
  }, [openAIStats?.syncStatus, openAIStats?.syncProgressCurrent]);

  const meetingSummarySettingsDirty =
    meetingSummariesEnabled !== Boolean(summary?.discordSettings.meetingSummariesEnabled) ||
    meetingSummaryMinParticipants !== String(summary?.discordSettings.meetingSummaryMinParticipants ?? 2) ||
    meetingSummaryMinDuration !== String(summary?.discordSettings.meetingSummaryMinDurationSeconds ?? 120) ||
    meetingSummaryLanguage !== (summary?.discordSettings.meetingSummaryLanguage ?? "English") ||
    meetingSummaryRecipient !== (summary?.discordSettings.meetingSummaryRecipient ?? "work_chat") ||
    meetingAudioRetention !== String(summary?.discordSettings.meetingAudioRetentionSeconds ?? 0);
  const meetingSummaryPromptDirty = meetingSummaryPrompt !== (summary?.discordSettings.meetingSummaryPrompt ?? "");
  const meetingSummaryTelegramTemplateDirty = meetingSummaryTelegramTemplate !== (summary?.discordSettings.meetingSummaryTelegramTemplate ?? "");

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

  async function saveMeetingSummarySettings() {
    if (settingsReadOnly) {
      return;
    }

    setSaving("meetingSummarySettings");
    setSaveStatus((items) => ({ ...items, meetingSummarySettings: undefined }));

    try {
      const response = await apiFetch("/api/v1/settings/discord", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meetingAutoAfkTimeoutSeconds: summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600,
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
      const response = await apiFetch("/api/v1/settings/discord", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meetingAutoAfkTimeoutSeconds: summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600,
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
      const response = await apiFetch("/api/v1/settings/discord", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meetingAutoAfkTimeoutSeconds: summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600,
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

  return (
    <>
      <div className="panel">
        <h2>Meeting Summaries</h2>
        <p className="settings-caption">
          Configure automatic Discord meeting summaries sent to the work Telegram chat.
        </p>
        <div className="settings-row meeting-summary-settings-row">
          <label>
            <span className="meeting-summary-setting-label">Meeting summaries</span>
            <span className="checkbox-cell">
              <input
                type="checkbox"
                checked={meetingSummariesEnabled}
                onChange={(event) => setMeetingSummariesEnabled(event.target.checked)}
                disabled={settingsReadOnly}
              />
              Enabled
            </span>
          </label>
          <label>
            <span className="meeting-summary-setting-label">Min participants</span>
            <input
              value={meetingSummaryMinParticipants}
              onChange={(event) => setMeetingSummaryMinParticipants(event.target.value)}
              type="number"
              min="1"
              disabled={settingsReadOnly}
            />
          </label>
          <label>
            <span className="meeting-summary-setting-label">Min duration, sec</span>
            <input
              value={meetingSummaryMinDuration}
              onChange={(event) => setMeetingSummaryMinDuration(event.target.value)}
              type="number"
              min="1"
              step="30"
              disabled={settingsReadOnly}
            />
          </label>
          <label>
            <span className="meeting-summary-setting-label">Summary language</span>
            <select value={meetingSummaryLanguage} onChange={(event) => setMeetingSummaryLanguage(event.target.value)} disabled={settingsReadOnly}>
              {MEETING_SUMMARY_LANGUAGES.map((language) => (
                <option value={language} key={language}>{language}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="meeting-summary-setting-label">Send summaries to</span>
            <select value={meetingSummaryRecipient} onChange={(event) => setMeetingSummaryRecipient(event.target.value)} disabled={settingsReadOnly}>
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
            <span className="meeting-summary-setting-label">Keep audio on server</span>
            <select value={meetingAudioRetention} onChange={(event) => setMeetingAudioRetention(event.target.value)} disabled={settingsReadOnly}>
              {MEETING_AUDIO_RETENTION_OPTIONS.map((option) => (
                <option value={String(option.value)} key={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <button
            className={settingsSaveButtonClassName(saveStatus.meetingSummarySettings)}
            onClick={() => void saveMeetingSummarySettings()}
            disabled={settingsReadOnly || saving === "meetingSummarySettings" || !meetingSummarySettingsDirty}
          >
            {settingsSaveButtonLabel("meetingSummarySettings", saving, saveStatus)}
          </button>
        </div>
      </div>
      <div className="meeting-summary-workspace" ref={workspaceRef}>
        <OpenAIStatsCard
          openAIStats={openAIStats}
          openAIStatsError={openAIStatsError}
          openAIStatsLoading={openAIStatsLoading}
          openAIStatsRefreshMode={openAIStatsRefreshMode}
          onRefresh={() => void loadOpenAIStats("month")}
          onRefreshTotals={() => void loadOpenAIStats("totals")}
        />
        <div className="meeting-summary-row">
          <RecentMeetingSummaryActivityCard meetingActivityItems={meetingActivityItems} meetingRecordingsError={meetingRecordingsError} period="today" />
          <SummaryInstructionsCard
            refCallback={promptPanelRef}
            meetingSummaryPrompt={meetingSummaryPrompt}
            settingsReadOnly={settingsReadOnly}
            saving={saving}
            saveStatus={saveStatus}
            meetingSummaryPromptDirty={meetingSummaryPromptDirty}
            onMeetingSummaryPromptChange={setMeetingSummaryPrompt}
            onSaveMeetingSummaryPrompt={() => void saveMeetingSummaryPrompt()}
          />
        </div>
        <div className="meeting-summary-row">
          <RecentMeetingSummaryActivityCard meetingActivityItems={meetingActivityItems} meetingRecordingsError={meetingRecordingsError} period="archive" />
          <TelegramSummaryFormatCard
            meetingSummaryTelegramTemplate={meetingSummaryTelegramTemplate}
            settingsReadOnly={settingsReadOnly}
            saving={saving}
            saveStatus={saveStatus}
            meetingSummaryTelegramTemplateDirty={meetingSummaryTelegramTemplateDirty}
            onMeetingSummaryTelegramTemplateChange={setMeetingSummaryTelegramTemplate}
            onSaveMeetingSummaryTelegramTemplate={() => void saveMeetingSummaryTelegramTemplate()}
          />
        </div>
      </div>
    </>
  );
}

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

function readCachedOpenAIStats(): OpenAIStats | null {
  try {
    const raw = readStorageItem(sessionBrowserStorage(), OPENAI_STATS_CACHE_KEY);
    return raw ? JSON.parse(raw) as OpenAIStats : null;
  } catch {
    return null;
  }
}

function writeCachedOpenAIStats(stats: OpenAIStats) {
  try {
    writeStorageCache(sessionBrowserStorage(), OPENAI_STATS_CACHE_KEY, JSON.stringify(stats));
  } catch {
    //
  }
}
