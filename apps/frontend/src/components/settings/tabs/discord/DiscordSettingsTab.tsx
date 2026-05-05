import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";

type DiscordSettingsTabProps = {
  discordAutoAfkTimeout: string;
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  discordSettingsDirty: boolean;
  onDiscordAutoAfkTimeoutChange: (value: string) => void;
  onSaveDiscordSettings: () => void;
};

export function DiscordSettingsTab({
  discordAutoAfkTimeout,
  settingsReadOnly,
  saving,
  saveStatus,
  discordSettingsDirty,
  onDiscordAutoAfkTimeoutChange,
  onSaveDiscordSettings
}: DiscordSettingsTabProps) {
  return (
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
            onChange={(event) => onDiscordAutoAfkTimeoutChange(event.target.value)}
            type="number"
            min="60"
            step="30"
            disabled={settingsReadOnly}
          />
        </label>
        <button className={settingsSaveButtonClassName(saveStatus.discord)} onClick={onSaveDiscordSettings} disabled={settingsReadOnly || saving === "discord" || !discordSettingsDirty}>
          {settingsSaveButtonLabel("discord", saving, saveStatus)}
        </button>
      </div>
    </div>
  );
}
