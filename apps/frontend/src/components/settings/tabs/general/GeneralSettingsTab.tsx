import { DiskUsageCard } from "../../DiskUsageCard";
import { ServicesStatusCard } from "../../ServicesStatusCard";
import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";
import type { Summary } from "../../../../types/dashboard";

type GeneralSettingsTabProps = {
  intervalSettings: Summary["intervalSettings"] | undefined;
  globalInterval: string;
  deviceInterval: string;
  idleThreshold: string;
  deviceIdleThreshold: string;
  pluginIngestEnabled: boolean;
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  isIntervalSettingsDirty: boolean;
  onGlobalIntervalChange: (value: string) => void;
  onDeviceIntervalChange: (value: string) => void;
  onIdleThresholdChange: (value: string) => void;
  onDeviceIdleThresholdChange: (value: string) => void;
  onPluginIngestEnabledChange: (value: boolean) => void;
  onSaveInterval: () => void;
};

export function GeneralSettingsTab({
  intervalSettings,
  globalInterval,
  deviceInterval,
  idleThreshold,
  deviceIdleThreshold,
  pluginIngestEnabled,
  settingsReadOnly,
  saving,
  saveStatus,
  isIntervalSettingsDirty,
  onGlobalIntervalChange,
  onDeviceIntervalChange,
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
            Device interval, sec
            <input value={deviceInterval} onChange={(event) => onDeviceIntervalChange(event.target.value)} type="number" min="1" disabled={settingsReadOnly} />
          </label>
          <label>
            Idle threshold, sec
            <input value={idleThreshold} onChange={(event) => onIdleThresholdChange(event.target.value)} type="number" min="30" disabled={settingsReadOnly} />
          </label>
          <label>
            Device idle threshold, sec
            <input value={deviceIdleThreshold} onChange={(event) => onDeviceIdleThresholdChange(event.target.value)} type="number" min="1" disabled={settingsReadOnly} />
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
          <p className="interval-settings-helper">
            Minimums: global interval 30 sec, device interval 1 sec, idle threshold 30 sec, device idle threshold 1 sec.
          </p>
        </div>
      </div>
      <div className="server-stats-card-row">
        <DiskUsageCard />
        <ServicesStatusCard />
      </div>
    </>
  );
}
