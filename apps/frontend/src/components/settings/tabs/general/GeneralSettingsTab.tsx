import { useEffect, useState } from "react";
import { apiFetch } from "../../../../api/client";
import { DiskUsageCard } from "../../DiskUsageCard";
import { ServicesStatusCard } from "../../ServicesStatusCard";
import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";
import type { Summary } from "../../../../types/dashboard";

type GeneralSettingsTabProps = {
  summary: Summary | null;
  settingsReadOnly: boolean;
  onSaved: () => void;
};

export function GeneralSettingsTab({
  summary,
  settingsReadOnly,
  onSaved
}: GeneralSettingsTabProps) {
  const [globalInterval, setGlobalInterval] = useState(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
  const [deviceInterval, setDeviceInterval] = useState(String(intervalSettingsDeviceInterval(summary)));
  const [idleThreshold, setIdleThreshold] = useState(String(intervalSettingsIdleThreshold(summary)));
  const [deviceIdleThreshold, setDeviceIdleThreshold] = useState(String(intervalSettingsDeviceIdleThreshold(summary)));
  const [pluginIngestEnabled, setPluginIngestEnabled] = useState(summary?.intervalSettings.pluginIngestEnabled ?? true);
  const [saving, setSaving] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, "saved" | "error" | undefined>>({});

  useEffect(() => {
    setGlobalInterval(String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300));
    setDeviceInterval(String(intervalSettingsDeviceInterval(summary)));
    setIdleThreshold(String(intervalSettingsIdleThreshold(summary)));
    setDeviceIdleThreshold(String(intervalSettingsDeviceIdleThreshold(summary)));
    setPluginIngestEnabled(summary?.intervalSettings.pluginIngestEnabled ?? true);
  }, [summary]);

  const savedGlobalInterval = String(summary?.intervalSettings.defaultSendIntervalSeconds ?? 300);
  const savedDeviceInterval = String(intervalSettingsDeviceInterval(summary));
  const savedIdleThreshold = String(intervalSettingsIdleThreshold(summary));
  const savedDeviceIdleThreshold = String(intervalSettingsDeviceIdleThreshold(summary));
  const savedPluginIngestEnabled = summary?.intervalSettings.pluginIngestEnabled ?? true;
  const isIntervalSettingsDirty =
    globalInterval !== savedGlobalInterval ||
    deviceInterval !== savedDeviceInterval ||
    idleThreshold !== savedIdleThreshold ||
    deviceIdleThreshold !== savedDeviceIdleThreshold ||
    pluginIngestEnabled !== savedPluginIngestEnabled;

  async function saveInterval() {
    if (settingsReadOnly) {
      return;
    }

    setSaving("interval");
    setSaveStatus((items) => ({ ...items, interval: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/intervals`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          defaultSendIntervalSeconds: Number(globalInterval),
          deviceSendIntervalSeconds: Number(deviceInterval),
          idleThresholdSeconds: Number(idleThreshold),
          deviceIdleThresholdSeconds: Number(deviceIdleThreshold),
          pluginIngestEnabled
        })
      });

      if (!response.ok) {
        throw new Error("Interval save failed");
      }

      setSaveStatus((items) => ({ ...items, interval: "saved" }));
      onSaved();
    } catch {
      setSaveStatus((items) => ({ ...items, interval: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setSaveStatus((items) => ({ ...items, interval: undefined }));
      }, 2500);
    }
  }

  return (
    <>
      <div className="panel" data-doc-target="settings-general-intervals">
        <h2>Send Interval</h2>
        <div className="settings-row">
          <label>
            Global interval, sec
            <input value={globalInterval} onChange={(event) => setGlobalInterval(event.target.value)} type="number" min="30" disabled={settingsReadOnly} />
          </label>
          <label>
            Device interval, sec
            <input value={deviceInterval} onChange={(event) => setDeviceInterval(event.target.value)} type="number" min="1" disabled={settingsReadOnly} />
          </label>
          <label>
            Idle threshold, sec
            <input value={idleThreshold} onChange={(event) => setIdleThreshold(event.target.value)} type="number" min="30" disabled={settingsReadOnly} />
          </label>
          <label>
            Device idle threshold, sec
            <input value={deviceIdleThreshold} onChange={(event) => setDeviceIdleThreshold(event.target.value)} type="number" min="1" disabled={settingsReadOnly} />
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
                  onChange={() => setPluginIngestEnabled(true)}
                  disabled={settingsReadOnly}
                />
                On
              </label>
              <label className="radio-inline">
                <input
                  type="radio"
                  name="plugin-ingest-enabled"
                  checked={!pluginIngestEnabled}
                  onChange={() => setPluginIngestEnabled(false)}
                  disabled={settingsReadOnly}
                />
                Off
              </label>
            </div>
          </div>
          <button className={settingsSaveButtonClassName(saveStatus.interval)} onClick={() => void saveInterval()} disabled={settingsReadOnly || saving === "interval" || !isIntervalSettingsDirty}>
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

function intervalSettingsIdleThreshold(summary: Summary | null) {
  const intervalSettings = summary?.intervalSettings as (Summary["intervalSettings"] & { idleThresholdSeconds?: number }) | undefined;
  return intervalSettings?.idleThresholdSeconds ?? 300;
}

function intervalSettingsDeviceInterval(summary: Summary | null) {
  const intervalSettings = summary?.intervalSettings as (Summary["intervalSettings"] & { deviceSendIntervalSeconds?: number }) | undefined;
  return intervalSettings?.deviceSendIntervalSeconds ?? summary?.intervalSettings.defaultSendIntervalSeconds ?? 300;
}

function intervalSettingsDeviceIdleThreshold(summary: Summary | null) {
  const intervalSettings = summary?.intervalSettings as (Summary["intervalSettings"] & { deviceIdleThresholdSeconds?: number }) | undefined;
  return intervalSettings?.deviceIdleThresholdSeconds ?? 300;
}
