import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";

type TelegramSummaryFormatCardProps = {
  meetingSummaryTelegramTemplate: string;
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  meetingSummaryTelegramTemplateDirty: boolean;
  onMeetingSummaryTelegramTemplateChange: (value: string) => void;
  onSaveMeetingSummaryTelegramTemplate: () => void;
};

export function TelegramSummaryFormatCard({
  meetingSummaryTelegramTemplate,
  settingsReadOnly,
  saving,
  saveStatus,
  meetingSummaryTelegramTemplateDirty,
  onMeetingSummaryTelegramTemplateChange,
  onSaveMeetingSummaryTelegramTemplate
}: TelegramSummaryFormatCardProps) {
  return (
    <div className="panel meeting-summary-prompt-panel" data-doc-target="settings-telegram-summary-format">
      <h3>Telegram summary format</h3>
      <p className="settings-caption">
        Template for the Telegram message sent after OpenAI creates the meeting summary. Available placeholders: {"{date}"}, {"{duration}"}, {"{participants}"}, {"{summary}"}.
      </p>
      <label className="meeting-summary-prompt-field">
        Prompt
        <textarea
          value={meetingSummaryTelegramTemplate}
          onChange={(event) => onMeetingSummaryTelegramTemplateChange(event.target.value)}
          rows={12}
          placeholder={"Meeting summary\nDate: {date}\nDuration: {duration}\nParticipants: {participants}\n\n{summary}"}
          disabled={settingsReadOnly}
        />
      </label>
      <button
        className={settingsSaveButtonClassName(saveStatus.meetingSummaryTelegramTemplate)}
        onClick={onSaveMeetingSummaryTelegramTemplate}
        disabled={settingsReadOnly || saving === "meetingSummaryTelegramTemplate" || !meetingSummaryTelegramTemplateDirty}
      >
        {settingsSaveButtonLabel("meetingSummaryTelegramTemplate", saving, saveStatus)}
      </button>
    </div>
  );
}
