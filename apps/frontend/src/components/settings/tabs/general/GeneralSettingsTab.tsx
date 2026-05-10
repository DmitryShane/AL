import { ServerStatsPanel } from "../../ServerStatsPanel";
import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";
import type { Summary } from "../../../../types/dashboard";

type GeneralSettingsTabProps = {
  intervalSettings: Summary["intervalSettings"] | undefined;
  globalInterval: string;
  idleThreshold: string;
  deviceIdleThreshold: string;
  pluginIngestEnabled: boolean;
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  isIntervalSettingsDirty: boolean;
  onGlobalIntervalChange: (value: string) => void;
  onIdleThresholdChange: (value: string) => void;
  onDeviceIdleThresholdChange: (value: string) => void;
  onPluginIngestEnabledChange: (value: boolean) => void;
  onSaveInterval: () => void;
};

export function GeneralSettingsTab({
  intervalSettings,
  globalInterval,
  idleThreshold,
  deviceIdleThreshold,
  pluginIngestEnabled,
  settingsReadOnly,
  saving,
  saveStatus,
  isIntervalSettingsDirty,
  onGlobalIntervalChange,
  onIdleThresholdChange,
  onDeviceIdleThresholdChange,
  onPluginIngestEnabledChange,
  onSaveInterval
}: GeneralSettingsTabProps) {
  return (
    <>
      <div className="panel">
        <h2>Send Interval</h2>
        <div className="settings-row">
          <label>
            Global interval, sec
            <input value={globalInterval} onChange={(event) => onGlobalIntervalChange(event.target.value)} type="number" min="30" disabled={settingsReadOnly} />
          </label>
          <label>
            Idle threshold, sec
            <input value={idleThreshold} onChange={(event) => onIdleThresholdChange(event.target.value)} type="number" min="30" disabled={settingsReadOnly} />
          </label>
          <label>
            Device idle threshold, sec
            <input value={deviceIdleThreshold} onChange={(event) => onDeviceIdleThresholdChange(event.target.value)} type="number" min="10" disabled={settingsReadOnly} />
          </label>
          <div className="plugin-ingest-field">
            <span id="plugin-ingest-heading" className="plugin-ingest-field-heading">
              Plugin reports
            </span>
            <div className="plugin-ingest-radios" role="radiogroup" aria-labelledby="plugin-ingest-heading">
              <label className="radio-inline">
                <input
                  type="radio"
                  name="plugin-ingest-enabled"
                  checked={pluginIngestEnabled}
                  onChange={() => onPluginIngestEnabledChange(true)}
                  disabled={settingsReadOnly}
                />
                On
              </label>
              <label className="radio-inline">
                <input
                  type="radio"
                  name="plugin-ingest-enabled"
                  checked={!pluginIngestEnabled}
                  onChange={() => onPluginIngestEnabledChange(false)}
                  disabled={settingsReadOnly}
                />
                Off
              </label>
            </div>
          </div>
          <button className={settingsSaveButtonClassName(saveStatus.interval)} onClick={onSaveInterval} disabled={settingsReadOnly || saving === "interval" || !isIntervalSettingsDirty}>
            {settingsSaveButtonLabel("interval", saving, saveStatus)}
          </button>
        </div>
      </div>
      <ServerStatsPanel />
    </>
  );
}
