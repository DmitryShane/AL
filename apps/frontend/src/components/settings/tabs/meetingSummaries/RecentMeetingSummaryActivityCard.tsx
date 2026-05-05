import type { MeetingActivityItem } from "../../../../types/dashboard";
import { formatReportMinutes, formatTimestamp, meetingActivityDetail, meetingActivityTitle } from "../../../../pages/pageHelpers";

type RecentMeetingSummaryActivityCardProps = {
  meetingActivityItems: MeetingActivityItem[];
  meetingRecordingsError: string;
};

export function RecentMeetingSummaryActivityCard({ meetingActivityItems, meetingRecordingsError }: RecentMeetingSummaryActivityCardProps) {
  return (
    <div className="panel meeting-summary-activity-panel">
      <h3>Recent meeting summary activity</h3>
      <p className="settings-caption">
        Live status for the Discord recording, OpenAI summary, and Telegram delivery pipeline.
      </p>
      <div className="meeting-summary-recordings-field">
        Process
        {meetingRecordingsError ? (
          <p className="empty">{meetingRecordingsError}</p>
        ) : meetingActivityItems.length ? (
          <div className="settings-list">
            {meetingActivityItems.map((item) => (
              <div className={item.itemType === "day_separator" ? "settings-list-day-separator" : "settings-list-item"} key={item.id}>
                <strong>{meetingActivityTitle(item)}</strong>
                {item.itemType !== "day_separator" ? renderMeetingActivityDetail(item) : null}
                {item.itemType === "recording" && item.recording.error ? <span className="alert-text">{item.recording.error}</span> : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="empty">No meeting summary activity yet.</p>
        )}
      </div>
    </div>
  );
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
  );
}
