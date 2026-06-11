import { useEffect, useState } from "react";
import { apiFetch } from "../../../../api/client";
import type { Summary } from "../../../../types/dashboard";
import { settingsSaveButtonClassName } from "../../../../pages/pageHelpers";

type TelegramSettingsTabProps = {
  summary: Summary | null;
  settingsReadOnly: boolean;
  onSaved: () => void;
};

export function TelegramSettingsTab({
  summary,
  settingsReadOnly,
  onSaved
}: TelegramSettingsTabProps) {
  const [telegramOnlinePromptDelayMinutes, setTelegramOnlinePromptDelayMinutes] = useState(
    String(intervalSettingsTelegramOnlinePromptMinutes(summary))
  );
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"saved" | "error" | undefined>();

  useEffect(() => {
    setTelegramOnlinePromptDelayMinutes(String(intervalSettingsTelegramOnlinePromptMinutes(summary)));
  }, [summary]);

  const dirty = telegramOnlinePromptDelayMinutes !== String(intervalSettingsTelegramOnlinePromptMinutes(summary));

  async function saveTelegramPromptSettings() {
    if (settingsReadOnly) {
      return;
    }

    setSaving(true);
    setSaveStatus(undefined);

    try {
      const minutes = Number(telegramOnlinePromptDelayMinutes);

      if (!Number.isFinite(minutes)) {
        throw new Error("Invalid minutes");
      }

      const response = await apiFetch("/api/v1/settings/intervals", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          telegramOnlinePromptDelayMinutes: minutes,
        }),
      });

      if (!response.ok) {
        throw new Error("Telegram prompt settings save failed");
      }

      setSaveStatus("saved");
      onSaved();
    } catch {
      setSaveStatus("error");
    } finally {
      setSaving(false);
      window.setTimeout(() => setSaveStatus(undefined), 2500);
    }
  }

  return (
    <div className="panel" data-doc-target="settings-telegram-panel">
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
            disabled={settingsReadOnly}
          />
        </label>
        <button
          className={settingsSaveButtonClassName(saveStatus)}
          onClick={() => void saveTelegramPromptSettings()}
          disabled={settingsReadOnly || saving || !dirty}
        >
          {saving ? "Saving..." : saveStatus === "saved" ? "Saved" : saveStatus === "error" ? "Error" : "Save"}
        </button>
      </div>
    </div>
  );
}

function intervalSettingsTelegramOnlinePromptMinutes(summary: Summary | null): number {
  const minutes = summary?.intervalSettings.telegramOnlinePromptDelayMinutes;

  if (typeof minutes === "number" && Number.isFinite(minutes) && minutes > 0) {
    return minutes;
  }

  return 5;
}
