import React from "react";
import { Activity, Box } from "lucide-react";
import cursorIconUrl from "../assets/cursor-icon.png";
import { REFRESH_INTERVAL_MS, REPORTS_PAGE_STORAGE_KEY, SETTINGS_TAB_STORAGE_KEY } from "../constants/dashboard";
import type { AuthorProfile, AuthorRow, DateRange, MeetingRecordingStatus, Report, SavedPrefab, SettingsTab, SiteUser, SiteUserRole, Summary } from "../types/dashboard";
export function settingsSaveButtonLabel(key: string, saving: string | null, statuses: Record<string, "saved" | "error" | undefined>) {
  if (saving === key) {
    return "Saving...";
  }

  if (statuses[key] === "saved") {
    return "Saved";
  }

  if (statuses[key] === "error") {
    return "Failed";
  }

  return "Save";
}

export function dashboardRefreshIntervalMs(summary: Summary | null) {
  const seconds = summary?.intervalSettings.defaultSendIntervalSeconds ?? REFRESH_INTERVAL_MS / 1000;
  return Math.max(1000, seconds * 1000);
}

export function formatSiteRole(role: SiteUserRole) {
  if (role === "admin") {
    return "Admin";
  }

  if (role === "editor") {
    return "Editor";
  }

  return "Viewer";
}

function humanizeSiteUserEmailLocalPart(email: string): string {
  const trimmed = email.trim();
  const local = trimmed.split("@")[0] || trimmed;

  if (!local) {
    return "User";
  }

  return local
    .replace(/[._-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

export function formatSiteUserSidebarLabel(user: SiteUser): string {
  const email = user.email.trim();
  const display = user.displayName.trim();

  if (display && display.toLowerCase() !== email.toLowerCase()) {
    return display;
  }

  return humanizeSiteUserEmailLocalPart(email);
}

export function settingsSaveButtonClassName(status: "saved" | "error" | undefined, outline = false) {
  const baseClassName = outline ? "primary-outline-button" : "primary-button";

  if (status === "saved") {
    return `${baseClassName} save-success`;
  }

  if (status === "error") {
    return `${baseClassName} save-error`;
  }

  return baseClassName;
}

export function meetingRecordingStatusLabel(recording: MeetingRecordingStatus) {
  if (recording.status === "recording") {
    return "Recording now";
  }

  if (recording.status === "uploading_audio") {
    return "Uploading audio to backend";
  }

  if (recording.status === "compressing_audio") {
    return "Compressing audio";
  }

  if (recording.status === "transcribing_openai") {
    return "Transcribing with OpenAI";
  }

  if (recording.status === "summarizing_openai") {
    return "Summarizing with OpenAI";
  }

  if (recording.status === "waiting_for_telegram" || recording.status === "summary_pending") {
    return "Summary created, waiting for Telegram";
  }

  if (recording.status === "telegram_claimed" || recording.status === "summary_claimed") {
    return "Telegram is sending the summary";
  }

  if (recording.status === "telegram_sent") {
    return "Summary sent to Telegram";
  }

  if (recording.status === "summary_failed") {
    return "Summary failed";
  }

  if (recording.status === "recording_failed") {
    return "Recording upload failed";
  }

  if (recording.status.startsWith("skipped_")) {
    return `Skipped: ${recording.status.replace("skipped_", "").replaceAll("_", " ")}`;
  }

  return recording.status.replaceAll("_", " ");
}

export function meetingRecordingDetail(recording: MeetingRecordingStatus) {
  const people = recording.participantNames?.length ? recording.participantNames.join(", ") : "No participants";
  const duration = recording.durationSeconds ? `, ${formatReportMinutes(recording.durationSeconds)}` : "";
  const recipient = meetingRecordingRecipientLabel(recording);
  const sentAt = recording.telegramSentAt ? `, sent ${formatTimestamp(recording.telegramSentAt)}` : "";
  const startedAt = recording.startedAt ? `Started ${formatTimestamp(recording.startedAt)}` : "Started time unknown";
  const updatedAt = recording.updatedAt ? ` Last update ${formatTimestamp(recording.updatedAt)}.` : "";
  const audioStats = meetingRecordingAudioStats(recording);

  return `${people}${duration}. ${startedAt}${recipient}${sentAt}.${audioStats}${updatedAt}`;
}

export function meetingRecordingRecipientLabel(recording: MeetingRecordingStatus) {
  if (!recording.recipient) {
    return "";
  }

  if (recording.recipient.kind === "private") {
    return `, recipient ${recording.recipient.label || "private chat"}`;
  }

  return ", recipient work chat";
}

export function meetingRecordingAudioStats(recording: MeetingRecordingStatus) {
  if (!recording.audioFrameCount && !recording.audioSizeBytes && !recording.corruptedPacketCount && !recording.audioQualityStatus) {
    return "";
  }

  const quality = recording.audioQualityStatus ? `, quality ${recording.audioQualityStatus}` : "";
  const mixedUsers = recording.mixedUserCount ? `, mixed users ${recording.mixedUserCount}` : "";
  const padding = recording.silencePaddingFrameCount ? `, padded frames ${recording.silencePaddingFrameCount}` : "";
  const unknown = recording.unknownSourceFrameCount ? `, unknown frames ${recording.unknownSourceFrameCount}` : "";
  const listenErrors = recording.listenErrorCount ? `, listen errors ${recording.listenErrorCount}` : "";
  const sizeMb = recording.audioSizeBytes ? `, file ${(recording.audioSizeBytes / 1024 / 1024).toFixed(2)} MB` : "";
  return ` Audio frames: ${recording.nonSilentFrameCount ?? 0}/${recording.audioFrameCount ?? 0}, corrupted packets: ${recording.corruptedPacketCount ?? 0}${quality}${mixedUsers}${padding}${unknown}${listenErrors}${sizeMb}.`;
}

export function emptyAuthorProfile(): AuthorProfile {
  return {
    rawAuthor: "",
    displayName: "",
    team: "",
    telegramUsername: "",
    discordUserId: "",
    discordUsername: "",
    pluginEnabled: true,
    autoBreakEnabled: false,
    autoBreakEffectiveDate: "",
    authorColor: "#13a37b",
    githubUsername: ""
  };
}

export function authorProfilePayload(profile: AuthorProfile) {
  return {
    rawAuthor: profile.rawAuthor,
    displayName: profile.displayName,
    team: profile.team ?? "",
    telegramUsername: profile.telegramUsername ?? "",
    discordUserId: profile.discordUserId ?? "",
    discordUsername: profile.discordUsername ?? "",
    pluginEnabled: profile.pluginEnabled ?? true,
    autoBreakEnabled: profile.autoBreakEnabled ?? false,
    autoBreakEffectiveDate: profile.autoBreakEffectiveDate ?? "",
    authorColor: profile.authorColor ?? "#13a37b",
    githubUsername: profile.githubUsername ?? ""
  };
}

export function autoBreakScheduleLabel(profile: AuthorProfile) {
  if (!profile.autoBreakEnabled) {
    return "Auto break is off";
  }

  if (profile.autoBreakEffectiveDate) {
    return `Starts ${profile.autoBreakEffectiveDate}`;
  }

  return "Starts next work day after save";
}

export function normalizeAuthorInput(value: string) {
  return value.trim().normalize("NFC");
}

export function activityColor(type: string) {
  const normalized = type.toLowerCase();

  if (normalized === "select") {
    return "#5b4dff";
  }

  if (normalized === "undo_redo") {
    return "#f59e0b";
  }

  if (normalized === "prefab_saved") {
    return "#13a37b";
  }

  if (normalized === "play_mode") {
    return "#0ea5e9";
  }

  if (normalized === "scene_saved") {
    return "#ef4444";
  }

  return paletteColor(normalized.length);
}

export function paletteColor(index: number) {
  const colors = ["#5b4dff", "#13a37b", "#f59e0b", "#0ea5e9", "#a855f7", "#ef4444", "#14b8a6"];
  return colors[index % colors.length];
}

export function savedFileLabel(prefab: SavedPrefab) {
  return prefab.name || prefab.path;
}

export function formatDuration(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

export function formatReportMinutes(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainingSeconds = rounded % 60;

  if (hours > 0) {
    if (remainingSeconds > 0) {
      return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(remainingSeconds).padStart(2, "0")}s`;
    }

    return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  }

  if (minutes > 0) {
    if (remainingSeconds > 0) {
      return `${minutes}m ${String(remainingSeconds).padStart(2, "0")}s`;
    }

    return `${minutes}m`;
  }

  return `${remainingSeconds}s`;
}

export function formatReportOvertime(seconds: number) {
  return seconds > 0 ? formatReportMinutes(seconds) : "-";
}

export function formatReportActive(report: Report) {
  return isNonActivityReport(report) ? "-" : formatReportMinutes(report.activeDeltaSeconds ?? 0);
}

export function formatReportIdle(report: Report) {
  return isNonActivityReport(report) ? "-" : formatReportMinutes(report.idleDeltaSeconds ?? 0);
}

export function isNonActivityReport(report: Report) {
  return report.source === "telegram" || report.reportType === "telegram" || report.source === "discord" || report.reportType === "meeting" || report.source === "status" || report.reportType === "status";
}

export function formatDurationDelta(seconds: number) {
  const prefix = seconds >= 0 ? "+" : "-";
  return `${prefix}${formatDuration(Math.abs(seconds))}`;
}

export function formatDelta(value: number) {
  const prefix = value >= 0 ? "+" : "";
  return `${prefix}${value.toFixed(1)}`;
}

export function monthIndexes() {
  return Array.from({ length: 12 }, (_, index) => index);
}

export function toCalendarDate(year: number, month: number, day: number) {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function calendarDayClassName(selected: boolean, isToday: boolean, isPast: boolean) {
  const classNames = ["calendar-day"];

  if (selected) {
    classNames.push("selected");
  }

  if (isToday) {
    classNames.push("today");
  }

  if (isPast) {
    classNames.push("locked");
  }

  return classNames.join(" ");
}

export function uniqueDates(dates: string[]) {
  return Array.from(new Set(dates)).sort();
}

export function dateRangeList(startDate: string, endDate: string) {
  const start = new Date(startDate);
  const end = new Date(endDate);
  const cursor = new Date(start <= end ? start : end);
  const last = new Date(start <= end ? end : start);
  const dates = [];

  while (cursor <= last) {
    dates.push(toDateInputValue(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }

  return dates;
}

export function formatMinutes(seconds: number) {
  return `${Math.max(0, Math.round(seconds / 60))}m`;
}

export function productivityClassName(productivity: number) {
  if (productivity > 100) {
    return "metric-value overdrive";
  }

  if (productivity > 80) {
    return "metric-value good";
  }

  if (productivity >= 50) {
    return "metric-value warning";
  }

  return "metric-value bad";
}

export function productivityTone(productivity: number) {
  if (productivity > 100) {
    return "overdrive";
  }

  if (productivity > 80) {
    return "good";
  }

  if (productivity >= 50) {
    return "warning";
  }

  return "bad";
}

export function authorCardProductivityTone(author: AuthorRow) {
  if (author.status === "stale" && !hasAuthorActivity(author)) {
    return "neutral";
  }

  return productivityTone(author.productivity);
}

export function hasAuthorActivity(author: AuthorRow) {
  return [
    author.activeSeconds,
    author.idleSeconds,
    author.meetingSeconds,
    author.breakSeconds,
    author.overtimeActiveSeconds,
    author.telegramDaySeconds,
    author.rawPluginDaySeconds ?? author.pluginDaySeconds
  ].some((value) => Number(value) > 0);
}

export function breakClassName(seconds: number) {
  return `metric-value ${breakTone(seconds)}`;
}

export function breakTone(seconds: number) {
  if (seconds <= 0) {
    return "neutral";
  }

  return seconds > 61 * 60 ? "bad" : "good";
}

export function formatSource(source?: string) {
  if (source === "ual") {
    return "Unity";
  }

  if (source === "bal") {
    return "Blender";
  }

  if (source === "fch") {
    return "FigmaWeb";
  }

  if (source === "fig") {
    return "FigmaApp";
  }

  if (source === "vsc") {
    return "VS Code";
  }

  if (source === "cur") {
    return "Cursor";
  }

  if (source === "telegram") {
    return "Telegram";
  }

  if (source === "discord") {
    return "Discord";
  }

  if (source === "status") {
    return "Status";
  }

  return source ?? "-";
}

export function sourceIcon(source?: string) {
  if (source === "ual") {
    return <Box size={16} />;
  }

  if (source === "bal") {
    return <BlenderIcon />;
  }

  if (source === "fch" || source === "fig") {
    return <FigmaIcon />;
  }

  if (source === "vsc") {
    return <VSCodeIcon />;
  }

  if (source === "cur") {
    return <CursorIcon />;
  }

  if (source === "telegram") {
    return <TelegramIcon />;
  }

  if (source === "discord") {
    return <DiscordIcon />;
  }

  if (source === "status") {
    return <Activity size={16} />;
  }

  return <Activity size={16} />;
}

function BlenderIcon() {
  return (
    <svg className="source-icon blender-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path className="blender-icon-mark" d="M9.1 3.1c-.5-.5-.5-1.2 0-1.7.5-.5 1.2-.5 1.7 0l4.6 4.4h3.5c.7 0 1.2.5 1.2 1.2s-.5 1.2-1.2 1.2h-1.1l1.9 1.8c1 .9 1.5 2.1 1.5 3.5 0 4-4.3 7.2-9.5 7.2-4.8 0-8.7-2.6-8.7-5.9 0-2.7 2.6-5 6.1-5.7H2.5c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2h8.3L9.1 5.2H5.6c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2h3.1l.4.3Z" />
      <path className="blender-icon-eye" d="M12.1 11.2c2.5 0 4.5 1.4 4.5 3.1s-2 3.1-4.5 3.1-4.5-1.4-4.5-3.1 2-3.1 4.5-3.1Z" />
      <path className="blender-icon-pupil" d="M12.1 12.8c1.2 0 2.1.7 2.1 1.5s-.9 1.5-2.1 1.5-2.1-.7-2.1-1.5.9-1.5 2.1-1.5Z" />
    </svg>
  );
}

function FigmaIcon() {
  return (
    <svg className="source-icon figma-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path className="figma-red" d="M12 2H8.4a3.4 3.4 0 1 0 0 6.8H12V2Z" />
      <path className="figma-purple" d="M12 8.8H8.4a3.4 3.4 0 1 0 0 6.8H12V8.8Z" />
      <path className="figma-blue" d="M15.6 8.8H12v6.8h3.6a3.4 3.4 0 1 0 0-6.8Z" />
      <path className="figma-green" d="M8.4 15.6H12V19a3.4 3.4 0 1 1-3.6-3.4Z" />
      <path className="figma-orange" d="M12 2h3.6a3.4 3.4 0 1 1 0 6.8H12V2Z" />
    </svg>
  );
}

function VSCodeIcon() {
  return (
    <svg className="source-icon vscode-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M17.9 2.4 7.6 11.8 3.2 8.4 1.8 9.2v5.6l1.4.8 4.4-3.4 10.3 9.4 4.3-1.7V4.1l-4.3-1.7Zm-.4 5.7v7.8l-5.8-3.9 5.8-3.9Z" />
    </svg>
  );
}

function CursorIcon() {
  return <img className="source-icon cursor-icon" src={cursorIconUrl} alt="" aria-hidden="true" />;
}

function TelegramIcon() {
  return (
    <svg className="source-icon telegram-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M21.8 4.1 18.6 20c-.2 1.1-.9 1.4-1.8.9l-5-3.7-2.4 2.3c-.3.3-.5.5-1 .5l.4-5.1 9.3-8.4c.4-.4-.1-.6-.6-.2L6 13.5 1.1 12c-1.1-.3-1.1-1.1.2-1.6L20.4 3c.9-.3 1.7.2 1.4 1.1Z" />
    </svg>
  );
}

function DiscordIcon() {
  return (
    <svg className="source-icon discord-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M19.5 5.4A16 16 0 0 0 15.5 4l-.2.4c1.5.4 2.7 1 3.8 1.8a12.9 12.9 0 0 0-4.7-1.5 13.8 13.8 0 0 0-4.8 0 12.9 12.9 0 0 0-4.7 1.5A11.8 11.8 0 0 1 8.7 4.4L8.5 4a16 16 0 0 0-4 1.4C2 9.1 1.4 12.7 1.7 16.2A16.1 16.1 0 0 0 6.6 18.7l.6-.8a10.4 10.4 0 0 1-1.6-.8l.4-.3c3.1 1.4 6.5 1.4 9.6 0l.4.3c-.5.3-1 .6-1.6.8l.6.8a16.1 16.1 0 0 0 4.9-2.5c.4-4-.7-7.6-2.4-10.8ZM8.5 14.2c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.7.8 1.6 1.8c0 1-.7 1.8-1.6 1.8Zm7 0c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.7.8 1.6 1.8c0 1-.7 1.8-1.6 1.8Z" />
    </svg>
  );
}

export function formatReportType(report: Report) {
  if (report.reportType === "status") {
    return report.statusEventType ?? report.activityType ?? "status";
  }

  if (report.reportType === "telegram") {
    return formatTelegramEvent(report.telegramEventType ?? report.activityType, report.telegramStatus);
  }

  if (report.reportType === "meeting") {
    return formatDiscordEvent(report.discordEventType ?? report.activityType, report.discordStatus);
  }

  if (report.reportType === "manual") {
    return "manual";
  }

  return "auto";
}

export function reportTypeBadgeClassName(reportType?: string) {
  if (reportType === "telegram" || reportType === "meeting" || reportType === "status") {
    return "report-type-badge manual";
  }

  if (reportType === "manual") {
    return "report-type-badge manual";
  }

  return "report-type-badge auto";
}

export function formatTelegramEvent(eventType?: string, status?: string) {
  if (status === "break_closed") {
    return "break off";
  }

  const labels: Record<string, string> = {
    online: "online",
    afk: "afk",
    offline: "offline",
    telegram_online: "online",
    telegram_afk: "afk",
    telegram_offline: "offline"
  };

  return labels[eventType ?? ""] ?? "telegram";
}

export function formatDiscordEvent(eventType?: string, status?: string) {
  if (status === "meeting_auto_afk") {
    return "auto leave";
  }

  const labels: Record<string, string> = {
    join: "meeting join",
    leave: "meeting leave",
    reconcile: "meeting live",
    meeting_join: "meeting join",
    meeting_leave: "meeting leave",
    meeting_reconcile: "meeting live"
  };

  return labels[eventType ?? ""] ?? "meeting";
}

export function formatActivityType(type: string) {
  const labels: Record<string, string> = {
    external: "External",
    play_mode: "Play Mode",
    prefab_saved: "Prefab Save",
    file_loaded: "File Load",
    file_saved: "File Save",
    scene_changed: "Scene Change",
    scene_saved: "Scene Save",
    select: "Select",
    undo_redo: "Undo/Redo"
  };

  return labels[type] ?? type;
}

export function formatAuthorTime(report: Report) {
  if (!report.recordedAt) {
    return "-";
  }

  const recordedAt = new Date(report.recordedAt);

  try {
    return new Intl.DateTimeFormat(undefined, {
      timeStyle: "short",
      timeZone: report.timeZoneId
    }).format(recordedAt);
  } catch {
    return formatOffsetTimestampTime(report.recordedAt);
  }
}

export function formatOffsetTimestampTime(value: string) {
  const match = value.match(/T(\d{2}):(\d{2})/);

  if (match) {
    return `${match[1]}:${match[2]}`;
  }

  return new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date(value));
}

export function formatTimeZoneLabel(report: Report) {
  if (report.timeZoneId) {
    const city = report.timeZoneId.split("/").pop()?.replace(/_/g, " ");

    if (city) {
      return city;
    }
  }

  return report.timeZoneDisplayName;
}

export function formatProfileTimeZoneLabel(profile: AuthorProfile) {
  if (profile.timeZoneId) {
    const city = profile.timeZoneId.split("/").pop()?.replace(/_/g, " ");

    if (city) {
      return city;
    }
  }

  return profile.timeZoneDisplayName || "-";
}

export function formatProfileTimeZoneTitle(profile: AuthorProfile) {
  if (profile.timeZoneId && profile.timeZoneDisplayName && profile.timeZoneDisplayName !== profile.timeZoneId) {
    return `${profile.timeZoneDisplayName} (${profile.timeZoneId})`;
  }

  return profile.timeZoneId || profile.timeZoneDisplayName || "Timezone will be detected from plugin reports";
}

export function formatTimestamp(value?: string) {
  if (!value) {
    return "-";
  }

  return new Date(value).toLocaleString();
}

export function matchesAuthorSearch(author: AuthorRow, search: string) {
  const query = search.trim().toLowerCase();

  if (!query) {
    return true;
  }

  return [author.displayName, author.authorEmail, author.rawAuthor, author.team, author.telegramUsername].some((value) =>
    value?.toLowerCase().includes(query)
  );
}

export function authorCardClassName(author: AuthorRow, active: boolean) {
  let presenceClass = "is-online";

  if (author.status === "stale") {
    presenceClass = isTelegramSignedOff(author.stalePresence) ? "is-telegram-offline" : "is-offline";
  }

  return `author-card ${active ? "active " : ""}${presenceClass} ${productivityTone(author.productivity)}`.trim();
}

export function authorMiniCardClassName(author: AuthorRow, active: boolean) {
  let presenceClass = "is-online";

  if (author.status === "stale") {
    presenceClass = isTelegramSignedOff(author.stalePresence) ? "is-telegram-offline" : "is-offline";
  }

  return `author-mini-card ${active ? "active " : ""}${presenceClass} ${productivityTone(author.productivity)}`.trim();
}

export function isTelegramSignedOff(stalePresence?: AuthorRow["stalePresence"]) {
  return stalePresence === "telegram" || stalePresence === "both";
}

export function compareAuthorCardStatus(left: AuthorRow, right: AuthorRow, dateRange: DateRange) {
  const leftSignedOff = isTelegramSignedOff(left.stalePresence);
  const rightSignedOff = isTelegramSignedOff(right.stalePresence);

  if (leftSignedOff !== rightSignedOff) {
    return leftSignedOff ? 1 : -1;
  }

  if (left.status !== right.status) {
    return left.status === "stale" ? 1 : -1;
  }

  if (left.status === "stale") {
    const leftLiveDate = isSelectedDateAuthorLocalToday(left, dateRange) && hasAuthorActivity(left);
    const rightLiveDate = isSelectedDateAuthorLocalToday(right, dateRange) && hasAuthorActivity(right);

    if (leftLiveDate !== rightLiveDate) {
      return leftLiveDate ? -1 : 1;
    }
  }

  return compareAuthorsByStatusAndProductivity(left, right);
}

export function compareAuthorsByStatusAndProductivity(left: AuthorRow, right: AuthorRow) {
  if (left.status !== right.status) {
    return left.status === "stale" ? 1 : -1;
  }

  const leftProd = Number.isFinite(left.productivity) ? left.productivity : 0;
  const rightProd = Number.isFinite(right.productivity) ? right.productivity : 0;
  const byProductivity = rightProd - leftProd;

  if (byProductivity !== 0) {
    return byProductivity;
  }

  return left.displayName.localeCompare(right.displayName);
}

export function isSelectedDateAuthorLocalToday(author: AuthorRow, dateRange: DateRange) {
  if (dateRange.startDate !== dateRange.endDate || dateRange.preset === "live") {
    return false;
  }

  return dateRange.startDate === authorLocalDate(author);
}

export function authorLocalDate(author: AuthorRow) {
  const now = new Date();
  const timeZone = author.timeZoneId;

  if (!timeZone) {
    return toDateInputValue(now);
  }

  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(now);
    const year = parts.find((part) => part.type === "year")?.value;
    const month = parts.find((part) => part.type === "month")?.value;
    const day = parts.find((part) => part.type === "day")?.value;

    if (year && month && day) {
      return `${year}-${month}-${day}`;
    }
  } catch {
    return toDateInputValue(now);
  }

  return toDateInputValue(now);
}

export function profileLocalTodayIso(profile: AuthorProfile) {
  const now = new Date();
  const timeZone = profile.timeZoneId?.trim();

  if (!timeZone) {
    return toDateInputValue(now);
  }

  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(now);
    const year = parts.find((part) => part.type === "year")?.value;
    const month = parts.find((part) => part.type === "month")?.value;
    const day = parts.find((part) => part.type === "day")?.value;

    if (year && month && day) {
      return `${year}-${month}-${day}`;
    }
  } catch {
    return toDateInputValue(now);
  }

  return toDateInputValue(now);
}

export function initials(value: string) {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export function avatarStyle(authorColor?: string) {
  return authorColor ? { backgroundColor: authorColor } : undefined;
}

export function loadSavedSettingsTab(): SettingsTab {
  const savedTab = localStorage.getItem(SETTINGS_TAB_STORAGE_KEY);

  if (
    savedTab === "general" ||
    savedTab === "authors" ||
    savedTab === "autoBreak" ||
    savedTab === "redirects" ||
    savedTab === "discord" ||
    savedTab === "telegram" ||
    savedTab === "meetingSummaries" ||
    savedTab === "users"
  ) {
    return savedTab;
  }

  return "general";
}

export function loadSavedReportsPage() {
  const savedPage = Number(localStorage.getItem(REPORTS_PAGE_STORAGE_KEY) ?? 1);

  if (Number.isInteger(savedPage) && savedPage > 0) {
    return savedPage;
  }

  return 1;
}

export function toDateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export type BulkActivityDeletePreset = "1d" | "2d" | "3d" | "week" | "month" | "full";

const BULK_PRESET_DAY_SPAN: Record<Exclude<BulkActivityDeletePreset, "full">, number> = {
  "1d": 0,
  "2d": 1,
  "3d": 2,
  week: 6,
  month: 29
};

/** Inclusive UTC calendar dates (YYYY-MM-DD), aligned with backend bulk-delete presets. */
export function bulkActivityDeleteUtcRange(preset: BulkActivityDeletePreset): { start: string; end: string } | null {
  if (preset === "full") {
    return null;
  }

  const now = new Date();
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - BULK_PRESET_DAY_SPAN[preset]);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);

  return { start: fmt(start), end: fmt(end) };
}
