import { useEffect, useState } from "react";
import { apiFetch } from "../../../../api/client";
import type { Summary } from "../../../../types/dashboard";
import { settingsSaveButtonClassName } from "../../../../pages/pageHelpers";

type DiscordSettingsTabProps = {
  summary: Summary | null;
  settingsReadOnly: boolean;
  onSaved: () => void;
};

export function DiscordSettingsTab({
  summary,
  settingsReadOnly,
  onSaved
}: DiscordSettingsTabProps) {
  const [discordAutoAfkTimeout, setDiscordAutoAfkTimeout] = useState(String(summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600));
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"saved" | "error" | undefined>();

  useEffect(() => {
    setDiscordAutoAfkTimeout(String(summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600));
  }, [summary]);

  const dirty = discordAutoAfkTimeout !== String(summary?.discordSettings.meetingAutoAfkTimeoutSeconds ?? 600);

  async function saveDiscordSettings() {
    if (settingsReadOnly) {
      return;
    }

    setSaving(true);
    setSaveStatus(undefined);

    try {
      const response = await apiFetch("/api/v1/settings/discord", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meetingAutoAfkTimeoutSeconds: Number(discordAutoAfkTimeout),
          meetingSummariesEnabled: Boolean(summary?.discordSettings.meetingSummariesEnabled),
          meetingSummaryMinParticipants: summary?.discordSettings.meetingSummaryMinParticipants ?? 2,
          meetingSummaryMinDurationSeconds: summary?.discordSettings.meetingSummaryMinDurationSeconds ?? 120,
          meetingSummaryLanguage: summary?.discordSettings.meetingSummaryLanguage ?? "English",
          meetingSummaryRecipient: summary?.discordSettings.meetingSummaryRecipient ?? "work_chat",
          meetingAudioRetentionSeconds: summary?.discordSettings.meetingAudioRetentionSeconds ?? 0,
          meetingSummaryPrompt: summary?.discordSettings.meetingSummaryPrompt ?? "",
          meetingSummaryTelegramTemplate: summary?.discordSettings.meetingSummaryTelegramTemplate ?? ""
        })
      });

      if (!response.ok) {
        throw new Error("Discord settings save failed");
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
    <div className="panel" data-doc-target="settings-discord-panel">
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
            disabled={settingsReadOnly}
          />
        </label>
        <button className={settingsSaveButtonClassName(saveStatus)} onClick={() => void saveDiscordSettings()} disabled={settingsReadOnly || saving || !dirty}>
          {saving ? "Saving..." : saveStatus === "saved" ? "Saved" : saveStatus === "error" ? "Error" : "Save"}
        </button>
      </div>
    </div>
  );
}
