import type { Ref } from "react";
import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";

type SummaryInstructionsCardProps = {
  refCallback: Ref<HTMLDivElement>;
  meetingSummaryPrompt: string;
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  meetingSummaryPromptDirty: boolean;
  onMeetingSummaryPromptChange: (value: string) => void;
  onSaveMeetingSummaryPrompt: () => void;
};

export function SummaryInstructionsCard({
  refCallback,
  meetingSummaryPrompt,
  settingsReadOnly,
  saving,
  saveStatus,
  meetingSummaryPromptDirty,
  onMeetingSummaryPromptChange,
  onSaveMeetingSummaryPrompt
}: SummaryInstructionsCardProps) {
  return (
    <div className="panel meeting-summary-prompt-panel" data-doc-target="settings-summary-instructions" ref={refCallback}>
      <h3>Summary instructions</h3>
      <p className="settings-caption">
        Prompt text used before the backend adds participants, required sections, language, and transcript automatically.
      </p>
      <label className="meeting-summary-prompt-field">
        Prompt
        <textarea
          value={meetingSummaryPrompt}
          onChange={(event) => onMeetingSummaryPromptChange(event.target.value)}
          rows={12}
          placeholder="Instructions for turning a meeting transcript into a Telegram summary. Participant names are added in the Telegram message header automatically; your text should only include the working sections (Discussed, Decisions, Action items, Open questions). The backend adds required section titles, language rules, expected participants for context, and the transcript."
          disabled={settingsReadOnly}
        />
      </label>
      <button
        className={settingsSaveButtonClassName(saveStatus.meetingSummaryPrompt)}
        onClick={onSaveMeetingSummaryPrompt}
        disabled={settingsReadOnly || saving === "meetingSummaryPrompt" || !meetingSummaryPromptDirty}
      >
        {settingsSaveButtonLabel("meetingSummaryPrompt", saving, saveStatus)}
      </button>
    </div>
  );
}
