import { useEffect, useState } from "react";
import { apiFetch } from "../../../../api/client";
import type { AuthorProfile } from "../../../../types/dashboard";
import type { MeetingNotificationSettings, Summary } from "../../../../types/dashboard";
import { settingsSaveButtonClassName } from "../../../../pages/pageHelpers";
import { AuthorAvatar } from "../../../AuthorAvatar";

const WEEKDAYS = [
  { value: 0, label: "Mon", title: "Monday" },
  { value: 1, label: "Tue", title: "Tuesday" },
  { value: 2, label: "Wed", title: "Wednesday" },
  { value: 3, label: "Thu", title: "Thursday" },
  { value: 4, label: "Fri", title: "Friday" }
];

type MeetingNotificationTabProps = {
  profiles: AuthorProfile[];
  summary: Summary | null;
  settingsReadOnly: boolean;
  onSaved: () => void;
};

export function MeetingNotificationTab({
  profiles,
  summary,
  settingsReadOnly,
  onSaved
}: MeetingNotificationTabProps) {
  const settings = meetingNotificationSettingsForUi(summary);
  const [enabled, setEnabled] = useState(settings.enabled);
  const [time, setTime] = useState(settings.time);
  const [timeZoneId, setTimeZoneId] = useState(settings.timeZoneId);
  const [daysOfWeek, setDaysOfWeek] = useState(settings.daysOfWeek);
  const [authorRawAuthors, setAuthorRawAuthors] = useState(settings.authorRawAuthors);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"saved" | "error" | undefined>();

  useEffect(() => {
    const nextSettings = meetingNotificationSettingsForUi(summary);
    setEnabled(nextSettings.enabled);
    setTime(nextSettings.time);
    setTimeZoneId(nextSettings.timeZoneId);
    setDaysOfWeek(nextSettings.daysOfWeek);
    setAuthorRawAuthors(nextSettings.authorRawAuthors);
  }, [summary]);

  const dirty =
    JSON.stringify(normalizeMeetingNotificationSettingsDraft({
      enabled,
      authorRawAuthors,
      time,
      timeZoneId,
      daysOfWeek
    })) !== JSON.stringify(normalizeMeetingNotificationSettingsDraft(settings));

  async function saveMeetingNotificationSettings() {
    if (settingsReadOnly) {
      return;
    }

    setSaving(true);
    setSaveStatus(undefined);

    try {
      const payload = normalizeMeetingNotificationSettingsDraft({
        enabled,
        authorRawAuthors,
        time,
        timeZoneId: timeZoneId || browserTimeZoneId(),
        daysOfWeek
      });
      const response = await apiFetch("/api/v1/settings/meeting-notification", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        throw new Error(await apiErrorDetail(response, "Meeting notification settings save failed"));
      }

      const data = await response.json() as MeetingNotificationSettings & { ok?: boolean };
      setEnabled(data.enabled ?? payload.enabled);
      setTime(data.time ?? payload.time);
      setTimeZoneId(data.timeZoneId ?? payload.timeZoneId);
      setDaysOfWeek(data.daysOfWeek ?? payload.daysOfWeek);
      setAuthorRawAuthors(data.authorRawAuthors ?? payload.authorRawAuthors);
      setSaveStatus("saved");
      onSaved();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Meeting notification settings save failed";
      window.alert(message);
      setSaveStatus("error");
    } finally {
      setSaving(false);
      window.setTimeout(() => setSaveStatus(undefined), 2500);
    }
  }

  function toggleWeekday(day: number) {
    const nextDays = daysOfWeek.includes(day)
      ? daysOfWeek.filter((item) => item !== day)
      : [...daysOfWeek, day].sort((left, right) => left - right);
    setDaysOfWeek(nextDays);
  }

  function toggleAuthor(rawAuthor: string) {
    const nextAuthors = authorRawAuthors.includes(rawAuthor)
      ? authorRawAuthors.filter((item) => item !== rawAuthor)
      : [...authorRawAuthors, rawAuthor];
    setAuthorRawAuthors(nextAuthors);
  }

  return (
    <div className="panel meeting-notification-panel" data-doc-target="settings-meeting-notification-panel">
      <h2>Meeting Notification</h2>
      <p className="settings-caption">
        Send one scheduled Telegram work chat message that mentions selected authors and asks them to join the Discord meeting channel.
      </p>

      <div className="meeting-notification-controls">
        <label className="checkbox-cell meeting-notification-enabled">
          <input
            type="checkbox"
            checked={enabled}
            disabled={settingsReadOnly}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          Enabled
        </label>
        <label>
          Send time
          <input
            type="time"
            value={time}
            disabled={settingsReadOnly}
            onChange={(event) => setTime(event.target.value)}
          />
        </label>
        <label>
          Timezone (auto)
          <span className="meeting-notification-timezone-value">{timeZoneId}</span>
        </label>
        <button
          className={settingsSaveButtonClassName(saveStatus)}
          onClick={() => void saveMeetingNotificationSettings()}
          disabled={settingsReadOnly || saving || !dirty}
        >
          {saving ? "Saving..." : saveStatus === "saved" ? "Saved" : saveStatus === "error" ? "Error" : "Save"}
        </button>
      </div>

      <div className="meeting-notification-weekdays">
        {WEEKDAYS.map((day) => (
          <label key={day.value} className="checkbox-cell" title={day.title}>
            <input
              type="checkbox"
              checked={daysOfWeek.includes(day.value)}
              disabled={settingsReadOnly}
              onChange={() => toggleWeekday(day.value)}
            />
            {day.label}
          </label>
        ))}
      </div>

      <div className="profile-table-shell meeting-notification-author-shell">
        <div className="auto-break-list">
          {profiles.length === 0 ? (
            <div className="fake-online-empty-state">No author profiles are available.</div>
          ) : null}
          {profiles.map((profile) => {
            const telegramUsername = (profile.telegramUsername ?? "").trim();
            const canMention = Boolean(telegramUsername);
            const selected = authorRawAuthors.includes(profile.rawAuthor);

            return (
              <div className="meeting-notification-author-row" key={profile.rawAuthor}>
                <div className="auto-break-identity">
                  <AuthorAvatar
                    displayName={profile.displayName || profile.rawAuthor}
                    authorColor={profile.authorColor}
                    avatarUrl={profile.avatarUrl}
                    variant="mini"
                  />
                  <div className="auto-break-identity-text">
                    <strong>{profile.displayName || profile.rawAuthor}</strong>
                    <small className="auto-break-raw-id">
                      {telegramUsername ? `@${telegramUsername.replace(/^@/, "")}` : "Telegram required"}
                    </small>
                  </div>
                </div>
                <label className="checkbox-cell">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={settingsReadOnly || !canMention}
                    onChange={() => toggleAuthor(profile.rawAuthor)}
                  />
                  Mention
                </label>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

const DEFAULT_MEETING_NOTIFICATION_SETTINGS: MeetingNotificationSettings = {
  configured: false,
  enabled: false,
  authorRawAuthors: [],
  time: "10:00",
  timeZoneId: browserTimeZoneId(),
  daysOfWeek: [0, 1, 2, 3, 4]
};

async function apiErrorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    const detail = payload.detail;

    if (typeof detail === "string") {
      return detail;
    }

    if (Array.isArray(detail) && detail[0] && typeof detail[0] === "object" && detail[0] !== null && "msg" in detail[0]) {
      return String((detail[0] as { msg: string }).msg);
    }
  } catch {
    //
  }

  return `${fallback} (HTTP ${response.status})`;
}

function browserTimeZoneId(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function meetingNotificationSettingsForUi(summary: Summary | null): MeetingNotificationSettings {
  const source = summary?.meetingNotificationSettings ?? DEFAULT_MEETING_NOTIFICATION_SETTINGS;
  return normalizeMeetingNotificationSettingsDraft(source);
}

function normalizeMeetingNotificationSettingsDraft(settings: MeetingNotificationSettings): MeetingNotificationSettings {
  const time = /^\d{2}:\d{2}$/.test(settings.time) ? settings.time : "10:00";
  const timeZoneId = (settings.timeZoneId || browserTimeZoneId()).trim() || "UTC";
  const daysOfWeek = Array.from(new Set(settings.daysOfWeek.filter((day) => Number.isInteger(day) && day >= 0 && day <= 6))).sort(
    (left, right) => left - right
  );
  const authorRawAuthors = Array.from(new Set(settings.authorRawAuthors.map((author) => author.trim()).filter(Boolean)));

  return {
    configured: settings.configured,
    enabled: Boolean(settings.enabled),
    authorRawAuthors,
    time,
    timeZoneId,
    daysOfWeek: daysOfWeek.length ? daysOfWeek : [0, 1, 2, 3, 4]
  };
}
