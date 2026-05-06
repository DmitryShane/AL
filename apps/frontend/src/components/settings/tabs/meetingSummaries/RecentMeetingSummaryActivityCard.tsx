import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DateRangePicker, type DateRange } from "../../../layout/DateRangePicker";
import type { MeetingActivityItem } from "../../../../types/dashboard";
import { formatReportMinutes, formatTimestamp, meetingActivityDetail, meetingActivityTitle } from "../../../../pages/pageHelpers";

type RecentMeetingSummaryActivityCardProps = {
  meetingActivityItems: MeetingActivityItem[];
  meetingRecordingsError: string;
  period: "today" | "archive";
};

export function RecentMeetingSummaryActivityCard({ meetingActivityItems, meetingRecordingsError, period }: RecentMeetingSummaryActivityCardProps) {
  const today = formatLocalDate(new Date());
  const [archiveDate, setArchiveDate] = useState("");
  const archiveDates = useMemo(() => {
    return Array.from(
      new Set(
        meetingActivityItems
          .map(getMeetingActivityDate)
          .filter((date) => date && date < today)
      )
    ).sort((a, b) => b.localeCompare(a));
  }, [meetingActivityItems, today]);

  useEffect(() => {
    if (period !== "archive" || archiveDate || !archiveDates.length) {
      return;
    }

    setArchiveDate(archiveDates[0]);
  }, [archiveDate, archiveDates, period]);

  const selectedArchiveDate = archiveDate || archiveDates[0] || "";
  const activityItems = period === "today"
    ? meetingActivityItems.filter((item) => getMeetingActivityDate(item) === today)
    : meetingActivityItems.filter((item) => {
      const date = getMeetingActivityDate(item);
      return Boolean(date && date < today && date === selectedArchiveDate);
    });
  const isToday = period === "today";
  const archiveDateRange: DateRange = {
    startDate: selectedArchiveDate,
    endDate: selectedArchiveDate,
    preset: "custom",
  };

  return (
    <MeetingSummaryActivityPanel
      title={isToday ? "Today's meeting summary activity" : "Meeting summary archive"}
      caption={isToday
        ? "Live status for today's Discord recording, OpenAI summary, and Telegram delivery pipeline."
        : "History for previous meeting days."}
      activityItems={activityItems}
      meetingRecordingsError={meetingRecordingsError}
      emptyMessage={isToday ? "No meeting summary activity for today yet." : "No previous meeting summary activity yet."}
      fieldAction={isToday || !selectedArchiveDate ? null : (
        <div className="meeting-summary-archive-date-picker">
          <DateRangePicker value={archiveDateRange} onChange={(range) => setArchiveDate(range.startDate)} showPresets={false} />
        </div>
      )}
    />
  );
}

type MeetingSummaryActivityPanelProps = {
  title: string;
  caption: string;
  activityItems: MeetingActivityItem[];
  meetingRecordingsError: string;
  emptyMessage: string;
  fieldAction?: ReactNode;
};

function MeetingSummaryActivityPanel({ title, caption, activityItems, meetingRecordingsError, emptyMessage, fieldAction }: MeetingSummaryActivityPanelProps) {
  return (
    <div className="panel meeting-summary-activity-panel">
      <h3>{title}</h3>
      <p className="settings-caption">{caption}</p>
      <div className="meeting-summary-recordings-field">
        <div className="meeting-summary-recordings-header">
          <span>Process</span>
          {fieldAction}
        </div>
        {meetingRecordingsError ? (
          <p className="empty">{meetingRecordingsError}</p>
        ) : activityItems.length ? (
          <div className="settings-list">
            {activityItems.map((item) => (
              <div className={item.itemType === "day_separator" ? "settings-list-day-separator" : "settings-list-item"} key={item.id}>
                <strong>{meetingActivityDisplayTitle(item)}</strong>
                {item.itemType !== "day_separator" ? renderMeetingActivityDetail(item) : null}
                {item.itemType === "recording" && item.recording.error ? <span className="alert-text">{item.recording.error}</span> : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="empty">{emptyMessage}</p>
        )}
      </div>
    </div>
  );
}

function getMeetingActivityDate(item: MeetingActivityItem) {
  if (item.date) {
    return item.date;
  }

  if (!item.timestamp) {
    return "";
  }

  return formatLocalDate(new Date(item.timestamp));
}

function formatLocalDate(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function meetingActivityDisplayTitle(item: MeetingActivityItem) {
  if (item.itemType === "day_separator") {
    return formatDisplayDate(item.date);
  }

  return meetingActivityTitle(item);
}

function formatDisplayDate(value: string) {
  const [year, month, day] = value.split("-");

  if (!year || !month || !day) {
    return value;
  }

  return `${day}-${month}-${year}`;
}

function renderMeetingActivityDetail(item: MeetingActivityItem) {
  if (item.itemType !== "recording") {
    return <span>{meetingActivityDetail(item)}</span>;
  }

  const recording = item.recording;
  const participants = recording.participantNames?.length ? recording.participantNames.join(", ") : "No participants";
  const duration = recording.durationSeconds ? formatReportMinutes(recording.durationSeconds) : "-";
  const recipient = recording.recipient?.kind === "private" ? recording.recipient.label || "private chat" : "work chat";
  const audioFrames = `${recording.nonSilentFrameCount ?? 0}/${recording.audioFrameCount ?? 0}`;
  const quality = recording.audioQualityStatus || "unknown";

  return (
    <>
      <div className="meeting-activity-detail-grid">
        <div>
          <span>Participants</span>
          <strong>{participants}</strong>
        </div>
        <div>
          <span>Timing</span>
          <strong>
            Started {formatTimestamp(recording.startedAt)}
            {recording.telegramSentAt ? `, sent ${formatTimestamp(recording.telegramSentAt)}` : ""}
            {recording.updatedAt ? `, updated ${formatTimestamp(recording.updatedAt)}` : ""}
          </strong>
        </div>
        <div>
          <span>Delivery</span>
          <strong>
            {recipient}, duration {duration}
          </strong>
        </div>
        <div>
          <span>Audio</span>
          <strong>
            Frames {audioFrames}, corrupted {recording.corruptedPacketCount ?? 0}, quality {quality}
          </strong>
        </div>
        <div>
          <span>Mix</span>
          <strong>
            Mixed users {recording.mixedUserCount ?? 0}
            {recording.silencePaddingFrameCount ? `, padded ${recording.silencePaddingFrameCount}` : ""}
            {recording.unknownSourceFrameCount ? `, unknown ${recording.unknownSourceFrameCount}` : ""}
            {recording.listenErrorCount ? `, listen errors ${recording.listenErrorCount}` : ""}
          </strong>
        </div>
      </div>
      {recording.telegramMessage ? (
        <div className="meeting-activity-telegram-message">
          <span>Telegram message</span>
          <pre>{recording.telegramMessage}</pre>
        </div>
      ) : null}
    </>
  );
}
