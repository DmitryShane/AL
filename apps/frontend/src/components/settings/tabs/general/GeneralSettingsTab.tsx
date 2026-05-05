import { ServerStatsPanel } from "../../ServerStatsPanel";
import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";
import type { AuthorProfile, Summary } from "../../../../types/dashboard";

type GeneralSettingsTabProps = {
  profiles: AuthorProfile[];
  authorIntervalDrafts: Record<string, string>;
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
  onAuthorIntervalChange: (author: string, value: string) => void;
  onSaveInterval: () => void;
  onSaveAuthorInterval: (author: string) => void;
};

export function GeneralSettingsTab({
  profiles,
  authorIntervalDrafts,
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
  onAuthorIntervalChange,
  onSaveInterval,
  onSaveAuthorInterval
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
            <input value={deviceIdleThreshold} onChange={(event) => onDeviceIdleThresholdChange(event.target.value)} type="number" min="30" disabled={settingsReadOnly} />
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
      <div className="panel">
        <h2>Author Interval Overrides</h2>
        <p className="settings-caption">
          Optional send interval overrides for individual authors. Clear a value to return that author to the global interval.
        </p>
        <div className="author-interval-list">
          {profiles.length ? profiles.map((profile) => {
            const rawAuthor = profile.rawAuthor;
            const savedInterval = authorIntervalSeconds(intervalSettings, rawAuthor);
            const draftInterval = authorIntervalDrafts[rawAuthor] ?? "";
            const saveKey = authorIntervalSaveKey(rawAuthor);
            const isDirty = draftInterval !== (savedInterval === null ? "" : String(savedInterval));

            return (
              <div className="author-interval-row" key={rawAuthor}>
                <div>
                  <strong>{profile.displayName || rawAuthor}</strong>
                  <span>{rawAuthor}</span>
                </div>
                <label>
                  Override interval, sec
                  <input
                    value={draftInterval}
                    onChange={(event) => onAuthorIntervalChange(rawAuthor, event.target.value)}
                    type="number"
                    min="30"
                    placeholder={String(intervalSettings?.defaultSendIntervalSeconds ?? 300)}
                    disabled={settingsReadOnly}
                  />
                </label>
                <button
                  className={settingsSaveButtonClassName(saveStatus[saveKey])}
                  onClick={() => onSaveAuthorInterval(rawAuthor)}
                  disabled={settingsReadOnly || saving === saveKey || !isDirty || (!draftInterval && savedInterval === null)}
                >
                  {settingsSaveButtonLabel(saveKey, saving, saveStatus)}
                </button>
              </div>
            );
          }) : (
            <p className="empty-state">Create author profiles to configure per-author intervals.</p>
          )}
        </div>
      </div>
      <ServerStatsPanel />
    </>
  );
}

export function authorIntervalSaveKey(author: string) {
  return `authorInterval:${author}`;
}

function authorIntervalSeconds(intervalSettings: Summary["intervalSettings"] | undefined, author: string) {
  const match = intervalSettings?.authors.find((item) => item.author === author);
  return match?.sendIntervalSeconds ?? null;
}
