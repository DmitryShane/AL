import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../../../api/client";
import type { AuthorProfile, FakeOnlineAvailableProfile, FakeOnlineSettings } from "../../../../types/dashboard";
import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";
import { AuthorAvatar } from "../../../AuthorAvatar";

const WEEKDAYS = [
  { value: 0, label: "Mon", title: "Monday" },
  { value: 1, label: "Tue", title: "Tuesday" },
  { value: 2, label: "Wed", title: "Wednesday" },
  { value: 3, label: "Thu", title: "Thursday" },
  { value: 4, label: "Fri", title: "Friday" }
];

type FakeOnlineTabProps = {
  profiles: AuthorProfile[];
};

export function FakeOnlineTab({ profiles }: FakeOnlineTabProps) {
  const [settings, setSettings] = useState<FakeOnlineSettings[]>([]);
  const [availableProfiles, setAvailableProfiles] = useState<FakeOnlineAvailableProfile[]>([]);
  const [drafts, setDrafts] = useState<Record<string, FakeOnlineSettings>>({});
  const [profileToAdd, setProfileToAdd] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, "saved" | "error" | undefined>>({});

  async function loadSettings() {
    const response = await apiFetch("/api/v1/settings/fake-online");

    if (!response.ok) {
      return;
    }

    const payload = await response.json();
    const rows: FakeOnlineSettings[] = payload.settings ?? [];
    const nextAvailableProfiles: FakeOnlineAvailableProfile[] = payload.availableProfiles ?? [];
    setSettings(rows);
    setAvailableProfiles(nextAvailableProfiles);
    setDrafts(Object.fromEntries(rows.map((row) => [row.rawAuthor, row])));
    setProfileToAdd((current) =>
      current && nextAvailableProfiles.some((profile) => profile.rawAuthor === current) ? current : ""
    );
  }

  useEffect(() => {
    void loadSettings();
  }, []);

  const profileByAuthor = useMemo(
    () => Object.fromEntries(profiles.map((profile) => [profile.rawAuthor, profile])),
    [profiles]
  );

  function updateDraft(rawAuthor: string, update: Partial<FakeOnlineSettings>) {
    setDrafts((items) => {
      const current = items[rawAuthor];
      if (!current) {
        return items;
      }

      return { ...items, [rawAuthor]: { ...current, ...update } };
    });
  }

  function toggleWeekday(rawAuthor: string, day: number) {
    const draft = drafts[rawAuthor];
    if (!draft) {
      return;
    }

    const nextDays = draft.daysOfWeek.includes(day)
      ? draft.daysOfWeek.filter((item) => item !== day)
      : [...draft.daysOfWeek, day].sort((left, right) => left - right);
    updateDraft(rawAuthor, { daysOfWeek: nextDays });
  }

  function isDirty(row: FakeOnlineSettings) {
    const draft = drafts[row.rawAuthor];
    if (!draft) {
      return false;
    }

    return JSON.stringify(normalizeSettingsDraft(draft)) !== JSON.stringify(normalizeSettingsDraft(row));
  }

  async function saveSettings(row: FakeOnlineSettings) {
    const draft = drafts[row.rawAuthor];
    if (!draft) {
      return;
    }

    setSaving(row.rawAuthor);
    setStatus((items) => ({ ...items, [row.rawAuthor]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/fake-online/${encodeURIComponent(row.rawAuthor)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(normalizeSettingsDraft(draft))
      });

      if (!response.ok) {
        throw new Error("Fake online settings save failed");
      }

      setStatus((items) => ({ ...items, [row.rawAuthor]: "saved" }));
      await loadSettings();
    } catch {
      setStatus((items) => ({ ...items, [row.rawAuthor]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setStatus((items) => ({ ...items, [row.rawAuthor]: undefined }));
      }, 1500);
    }
  }

  async function addProfile() {
    const profile = availableProfiles.find((item) => item.rawAuthor === profileToAdd);

    if (!profile) {
      return;
    }

    setSaving("addProfile");
    setStatus((items) => ({ ...items, addProfile: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/fake-online/${encodeURIComponent(profile.rawAuthor)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: false,
          daysOfWeek: [],
          startTime: "10:00",
          endTime: "12:00",
          delayMinSeconds: 5,
          delayMaxSeconds: 60
        })
      });

      if (!response.ok) {
        throw new Error("Fake online profile add failed");
      }

      setStatus((items) => ({ ...items, addProfile: "saved" }));
      await loadSettings();
    } catch {
      setStatus((items) => ({ ...items, addProfile: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setStatus((items) => ({ ...items, addProfile: undefined }));
      }, 1500);
    }
  }

  async function removeProfile(row: FakeOnlineSettings) {
    setSaving(`delete:${row.rawAuthor}`);
    setStatus((items) => ({ ...items, [`delete:${row.rawAuthor}`]: undefined }));

    try {
      const response = await apiFetch(`/api/v1/settings/fake-online/${encodeURIComponent(row.rawAuthor)}`, {
        method: "DELETE"
      });

      if (!response.ok) {
        throw new Error("Fake online profile remove failed");
      }

      setStatus((items) => ({ ...items, [`delete:${row.rawAuthor}`]: "saved" }));
      await loadSettings();
    } catch {
      setStatus((items) => ({ ...items, [`delete:${row.rawAuthor}`]: "error" }));
    } finally {
      setSaving(null);
      window.setTimeout(() => {
        setStatus((items) => ({ ...items, [`delete:${row.rawAuthor}`]: undefined }));
      }, 1500);
    }
  }

  return (
    <div className="panel" data-doc-target="settings-fake-online-panel">
      <h2>Fake Online</h2>
      <p className="settings-caption">
        Send a scheduled Telegram online prompt for selected author profiles, then auto-confirm it after a randomized delay.
      </p>
      <div className="fake-online-add-card">
        <label>
          Author Profile
          <select value={profileToAdd} onChange={(event) => setProfileToAdd(event.target.value)}>
            <option value="">Select profile</option>
            {availableProfiles.map((profile) => (
              <option key={profile.rawAuthor} value={profile.rawAuthor} disabled={!profile.canEnable}>
                {profile.displayName || profile.rawAuthor}
                {profile.telegramUsername ? ` (@${profile.telegramUsername})` : " - Telegram required"}
              </option>
            ))}
          </select>
        </label>
        <button
          className={settingsSaveButtonClassName(status.addProfile, false)}
          onClick={() => void addProfile()}
          disabled={saving === "addProfile" || !profileToAdd}
        >
          {settingsSaveButtonLabel("addProfile", saving, status) === "Save" ? "Add profile" : settingsSaveButtonLabel("addProfile", saving, status)}
        </button>
      </div>
      <div className="profile-table-shell">
        <div className="auto-break-list">
          {settings.length === 0 ? (
            <div className="fake-online-empty-state">Add an author profile to configure fake online scheduling.</div>
          ) : null}
          {settings.map((row) => {
            const draft = drafts[row.rawAuthor] ?? row;
            const profile = profileByAuthor[row.rawAuthor];
            const disabled = !row.canEnable;
            return (
              <div className="fake-online-row" key={row.rawAuthor}>
                <div className="auto-break-identity">
                  <AuthorAvatar
                    displayName={row.displayName || row.rawAuthor}
                    authorColor={profile?.authorColor}
                    avatarUrl={profile?.avatarUrl}
                    variant="mini"
                  />
                  <div className="auto-break-identity-text">
                    <strong>{row.displayName || row.rawAuthor}</strong>
                    <small className="auto-break-raw-id">
                      {(row.authorEmail ?? "").trim() || row.rawAuthor}
                    </small>
                  </div>
                </div>
                <label className="checkbox-cell">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    disabled={disabled}
                    onChange={(event) => updateDraft(row.rawAuthor, { enabled: event.target.checked })}
                  />
                  Enabled
                </label>
                <div className="fake-online-weekdays">
                  {WEEKDAYS.map((day) => (
                    <label key={day.value} className="checkbox-cell" title={day.title}>
                      <input
                        type="checkbox"
                        checked={draft.daysOfWeek.includes(day.value)}
                        disabled={disabled}
                        onChange={() => toggleWeekday(row.rawAuthor, day.value)}
                      />
                      {day.label}
                    </label>
                  ))}
                </div>
                <label>
                  Start
                  <input
                    type="time"
                    value={draft.startTime}
                    disabled={disabled}
                    onChange={(event) => updateDraft(row.rawAuthor, { startTime: event.target.value })}
                  />
                </label>
                <label>
                  End
                  <input
                    type="time"
                    value={draft.endTime}
                    disabled={disabled}
                    onChange={(event) => updateDraft(row.rawAuthor, { endTime: event.target.value })}
                  />
                </label>
                <label>
                  Delay min, sec
                  <input
                    type="number"
                    min={0}
                    max={3600}
                    value={draft.delayMinSeconds}
                    disabled={disabled}
                    onChange={(event) => updateDraft(row.rawAuthor, { delayMinSeconds: Number(event.target.value) })}
                  />
                </label>
                <label>
                  Delay max, sec
                  <input
                    type="number"
                    min={0}
                    max={3600}
                    value={draft.delayMaxSeconds}
                    disabled={disabled}
                    onChange={(event) => updateDraft(row.rawAuthor, { delayMaxSeconds: Number(event.target.value) })}
                  />
                </label>
                <button
                  className={settingsSaveButtonClassName(status[row.rawAuthor], true)}
                  onClick={() => void saveSettings(row)}
                  disabled={disabled || saving === row.rawAuthor || !isDirty(row)}
                >
                  {settingsSaveButtonLabel(row.rawAuthor, saving, status)}
                </button>
                <button
                  className={settingsSaveButtonClassName(status[`delete:${row.rawAuthor}`], true)}
                  onClick={() => void removeProfile(row)}
                  disabled={saving === `delete:${row.rawAuthor}`}
                >
                  Remove
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function normalizeSettingsDraft(settings: FakeOnlineSettings) {
  return {
    enabled: settings.enabled,
    daysOfWeek: [...settings.daysOfWeek].filter((day) => day >= 0 && day <= 4).sort((left, right) => left - right),
    startTime: settings.startTime,
    endTime: settings.endTime,
    delayMinSeconds: Number(settings.delayMinSeconds),
    delayMaxSeconds: Number(settings.delayMaxSeconds)
  };
}
