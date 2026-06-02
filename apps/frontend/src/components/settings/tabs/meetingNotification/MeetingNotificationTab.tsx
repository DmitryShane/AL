import type { AuthorProfile } from "../../../../types/dashboard";
import { settingsSaveButtonClassName, settingsSaveButtonLabel } from "../../../../pages/pageHelpers";
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
  enabled: boolean;
  time: string;
  timeZoneId: string;
  daysOfWeek: number[];
  authorRawAuthors: string[];
  settingsReadOnly: boolean;
  saving: string | null;
  saveStatus: Record<string, "saved" | "error" | undefined>;
  dirty: boolean;
  onEnabledChange: (value: boolean) => void;
  onTimeChange: (value: string) => void;
  onDaysOfWeekChange: (value: number[]) => void;
  onAuthorRawAuthorsChange: (value: string[]) => void;
  onSave: () => void;
};

export function MeetingNotificationTab({
  profiles,
  enabled,
  time,
  timeZoneId,
  daysOfWeek,
  authorRawAuthors,
  settingsReadOnly,
  saving,
  saveStatus,
  dirty,
  onEnabledChange,
  onTimeChange,
  onDaysOfWeekChange,
  onAuthorRawAuthorsChange,
  onSave
}: MeetingNotificationTabProps) {
  function toggleWeekday(day: number) {
    const nextDays = daysOfWeek.includes(day)
      ? daysOfWeek.filter((item) => item !== day)
      : [...daysOfWeek, day].sort((left, right) => left - right);
    onDaysOfWeekChange(nextDays);
  }

  function toggleAuthor(rawAuthor: string) {
    const nextAuthors = authorRawAuthors.includes(rawAuthor)
      ? authorRawAuthors.filter((item) => item !== rawAuthor)
      : [...authorRawAuthors, rawAuthor];
    onAuthorRawAuthorsChange(nextAuthors);
  }

  return (
    <div className="panel meeting-notification-panel">
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
            onChange={(event) => onEnabledChange(event.target.checked)}
          />
          Enabled
        </label>
        <label>
          Send time
          <input
            type="time"
            value={time}
            disabled={settingsReadOnly}
            onChange={(event) => onTimeChange(event.target.value)}
          />
        </label>
        <label>
          Timezone (auto)
          <span className="meeting-notification-timezone-value">{timeZoneId}</span>
        </label>
        <button
          className={settingsSaveButtonClassName(saveStatus.meetingNotification)}
          onClick={onSave}
          disabled={settingsReadOnly || saving === "meetingNotification" || !dirty}
        >
          {settingsSaveButtonLabel("meetingNotification", saving, saveStatus)}
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
