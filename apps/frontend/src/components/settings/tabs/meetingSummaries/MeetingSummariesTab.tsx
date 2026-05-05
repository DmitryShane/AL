import type { Ref } from "react";
import { MEETING_AUDIO_RETENTION_OPTIONS, MEETING_SUMMARY_LANGUAGES } from "../../../../constants/dashboard";
import type { AuthorProfile, MeetingActivityItem, OpenAIStats } from "../../../../types/dashboard";
import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";
import { OpenAIStatsCard } from "./OpenAIStatsCard";
import { RecentMeetingSummaryActivityCard } from "./RecentMeetingSummaryActivityCard";
import { SummaryInstructionsCard } from "./SummaryInstructionsCard";

type MeetingSummariesTabProps = {
  workspaceRef: Ref<HTMLDivElement>;
  promptPanelRef: Ref<HTMLDivElement>;
  profiles: AuthorProfile[];
  meetingSummariesEnabled: boolean;
  meetingSummaryMinParticipants: string;
  meetingSummaryMinDuration: string;
  meetingSummaryLanguage: string;
  meetingSummaryRecipient: string;
  meetingAudioRetention: string;
  meetingSummaryPrompt: string;
  meetingActivityItems: MeetingActivityItem[];
  meetingRecordingsError: string;
  openAIStats: OpenAIStats | null;
  openAIStatsError: string;
  openAIStatsLoading: boolean;
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  meetingSummarySettingsDirty: boolean;
  meetingSummaryPromptDirty: boolean;
  onMeetingSummariesEnabledChange: (value: boolean) => void;
  onMeetingSummaryMinParticipantsChange: (value: string) => void;
  onMeetingSummaryMinDurationChange: (value: string) => void;
  onMeetingSummaryLanguageChange: (value: string) => void;
  onMeetingSummaryRecipientChange: (value: string) => void;
  onMeetingAudioRetentionChange: (value: string) => void;
  onMeetingSummaryPromptChange: (value: string) => void;
  onSaveMeetingSummarySettings: () => void;
  onSaveMeetingSummaryPrompt: () => void;
  onRefreshOpenAIStats: () => void;
};

export function MeetingSummariesTab({
  workspaceRef,
  promptPanelRef,
  profiles,
  meetingSummariesEnabled,
  meetingSummaryMinParticipants,
  meetingSummaryMinDuration,
  meetingSummaryLanguage,
  meetingSummaryRecipient,
  meetingAudioRetention,
  meetingSummaryPrompt,
  meetingActivityItems,
  meetingRecordingsError,
  openAIStats,
  openAIStatsError,
  openAIStatsLoading,
  settingsReadOnly,
  saving,
  saveStatus,
  meetingSummarySettingsDirty,
  meetingSummaryPromptDirty,
  onMeetingSummariesEnabledChange,
  onMeetingSummaryMinParticipantsChange,
  onMeetingSummaryMinDurationChange,
  onMeetingSummaryLanguageChange,
  onMeetingSummaryRecipientChange,
  onMeetingAudioRetentionChange,
  onMeetingSummaryPromptChange,
  onSaveMeetingSummarySettings,
  onSaveMeetingSummaryPrompt,
  onRefreshOpenAIStats
}: MeetingSummariesTabProps) {
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
                onChange={(event) => onMeetingSummariesEnabledChange(event.target.checked)}
                disabled={settingsReadOnly}
              />
              Enabled
            </span>
          </label>
          <label>
            <span className="meeting-summary-setting-label">Min participants</span>
            <input
              value={meetingSummaryMinParticipants}
              onChange={(event) => onMeetingSummaryMinParticipantsChange(event.target.value)}
              type="number"
              min="1"
              disabled={settingsReadOnly}
            />
          </label>
          <label>
            <span className="meeting-summary-setting-label">Min duration, sec</span>
            <input
              value={meetingSummaryMinDuration}
              onChange={(event) => onMeetingSummaryMinDurationChange(event.target.value)}
              type="number"
              min="1"
              step="30"
              disabled={settingsReadOnly}
            />
          </label>
          <label>
            <span className="meeting-summary-setting-label">Summary language</span>
            <select value={meetingSummaryLanguage} onChange={(event) => onMeetingSummaryLanguageChange(event.target.value)} disabled={settingsReadOnly}>
              {MEETING_SUMMARY_LANGUAGES.map((language) => (
                <option value={language} key={language}>{language}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="meeting-summary-setting-label">Send summaries to</span>
            <select value={meetingSummaryRecipient} onChange={(event) => onMeetingSummaryRecipientChange(event.target.value)} disabled={settingsReadOnly}>
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
            <select value={meetingAudioRetention} onChange={(event) => onMeetingAudioRetentionChange(event.target.value)} disabled={settingsReadOnly}>
              {MEETING_AUDIO_RETENTION_OPTIONS.map((option) => (
                <option value={String(option.value)} key={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <button
            className={settingsSaveButtonClassName(saveStatus.meetingSummarySettings)}
            onClick={onSaveMeetingSummarySettings}
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
          onRefresh={onRefreshOpenAIStats}
        />
        <div className="meeting-summary-main-row">
          <RecentMeetingSummaryActivityCard meetingActivityItems={meetingActivityItems} meetingRecordingsError={meetingRecordingsError} />
          <SummaryInstructionsCard
            refCallback={promptPanelRef}
            meetingSummaryPrompt={meetingSummaryPrompt}
            settingsReadOnly={settingsReadOnly}
            saving={saving}
            saveStatus={saveStatus}
            meetingSummaryPromptDirty={meetingSummaryPromptDirty}
            onMeetingSummaryPromptChange={onMeetingSummaryPromptChange}
            onSaveMeetingSummaryPrompt={onSaveMeetingSummaryPrompt}
          />
        </div>
      </div>
    </>
  );
}
