import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";

type TelegramSettingsTabProps = {
  telegramOnlinePromptDelayMinutes: string;
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  telegramPromptSettingsDirty: boolean;
  onTelegramOnlinePromptDelayMinutesChange: (value: string) => void;
  onSaveTelegramPromptSettings: () => void;
};

export function TelegramSettingsTab({
  telegramOnlinePromptDelayMinutes,
  settingsReadOnly,
  saving,
  saveStatus,
  telegramPromptSettingsDirty,
  onTelegramOnlinePromptDelayMinutesChange,
  onSaveTelegramPromptSettings
}: TelegramSettingsTabProps) {
  return (
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
            onChange={(event) => onTelegramOnlinePromptDelayMinutesChange(event.target.value)}
            type="number"
            min="1"
            max="1440"
            step="1"
            disabled={settingsReadOnly}
          />
        </label>
        <button
          className={settingsSaveButtonClassName(saveStatus.telegramPrompt)}
          onClick={onSaveTelegramPromptSettings}
          disabled={settingsReadOnly || saving === "telegramPrompt" || !telegramPromptSettingsDirty}
        >
          {settingsSaveButtonLabel("telegramPrompt", saving, saveStatus)}
        </button>
      </div>
    </div>
  );
}
