export type Page = "authors" | "activity" | "analytics" | "calendar" | "alerts" | "settings";
export type SettingsTab = "general" | "authors" | "autoBreak" | "redirects" | "discord" | "telegram" | "meetingSummaries" | "users";

export type Health = {
  ok: boolean;
  mongo: boolean;
};

export type Report = {
  source?: string;
  author?: string;
  displayName?: string;
  team?: string;
  date?: string;
  activeDeltaSeconds?: number;
  idleDeltaSeconds?: number;
  overtimeActiveDeltaSeconds?: number;
  recordedAt?: string;
  receivedAt?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  reportType?: string;
  activityType?: string;
  telegramEventType?: string;
  telegramStatus?: string;
  discordEventType?: string;
  discordStatus?: string;
  statusEventType?: string;
  statusReason?: string;
  pluginVersion?: string;
};

export type AuthorRow = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  telegramUsername?: string;
  telegramPrivateChatId?: number;
  discordUserId?: string;
  discordUsername?: string;
  authorColor?: string;
  avatarUrl?: string;
  autoBreakEnabled?: boolean;
  source?: string;
  pluginVersion?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  lastRecordedAt?: string;
  lastReceivedAt?: string;
  daySeconds: number;
  telegramDaySeconds: number;
  pluginDaySeconds: number;
  telegramToFirstActivitySeconds?: number;
  activeSeconds: number;
  idleSeconds: number;
  meetingSeconds: number;
  breakSeconds: number;
  overtimeActiveSeconds: number;
  rawPluginDaySeconds?: number;
  productivity: number;
  activityMix?: ActivityCount[];
  savedPrefabs?: SavedPrefab[];
  overtimeActivityMix?: ActivityCount[];
  overtimeSavedPrefabs?: SavedPrefab[];
  activityMixBySource?: ActivityMixSourceGroup[];
  savedPrefabsBySource?: SavedPrefabsSourceGroup[];
  overtimeActivityMixBySource?: ActivityMixSourceGroup[];
  overtimeSavedPrefabsBySource?: SavedPrefabsSourceGroup[];
  status?: "online" | "stale";
  stalePresence?: "telegram" | "reports" | "both";
};

export type ActivitySummary = {
  totals: {
    daySeconds: number;
    telegramDaySeconds: number;
    pluginDaySeconds: number;
    rawPluginDaySeconds?: number;
    telegramToFirstActivitySeconds?: number;
    activeSeconds: number;
    idleSeconds: number;
    meetingSeconds: number;
    breakSeconds: number;
    overtimeActiveSeconds: number;
  };
  authors: AuthorRow[];
  profiles: AuthorProfile[];
  authorAliases?: AuthorAlias[];
  activityMix: ActivityCount[];
  savedPrefabs: SavedPrefab[];
  overtimeActivityMix?: ActivityCount[];
  overtimeSavedPrefabs?: SavedPrefab[];
  hourlyActivityByAuthor: AuthorHourlyActivity[];
  cache?: {
    hit?: boolean;
    key?: string;
  };
};

export type AuthorAlias = {
  sourceRawAuthor: string;
  targetRawAuthor: string;
};

export type AuthorProfile = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  telegramUsername?: string;
  telegramPrivateChatId?: number;
  discordUserId?: string;
  discordUsername?: string;
  pluginEnabled?: boolean;
  autoBreakEnabled?: boolean;
  autoBreakEffectiveDate?: string;
  authorColor?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  githubUsername?: string;
  avatarUrl?: string;
};

export type ActivityCount = {
  type: string;
  count: number;
  percent: number;
};

export type SavedPrefab = {
  path: string;
  name: string;
  projectId?: string;
  saveCount: number;
};

export type ActivityMixSourceGroup = {
  source: string;
  totalCount: number;
  activityMix: ActivityCount[];
};

export type SavedPrefabsSourceGroup = {
  source: string;
  totalSaveCount: number;
  savedPrefabs: SavedPrefab[];
};

export type HourlyActivity = {
  hour: number;
  activeSeconds: number;
  idleSeconds: number;
  breakSeconds?: number;
  meetingSeconds?: number;
  overtimeActiveSeconds?: number;
};

export type AuthorHourlyActivity = {
  author: string;
  rawAuthor?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  hourlyActivity: HourlyActivity[];
};

export type Summary = {
  authors: string[];
  reports: Report[];
  intervalSettings: {
    defaultSendIntervalSeconds: number;
    idleThresholdSeconds: number;
    deviceIdleThresholdSeconds: number;
    pluginIngestEnabled: boolean;
    telegramOnlinePromptDelayMinutes: number;
    avatarRefreshCadence?: "week" | "month";
    authors: Array<{ author: string; sendIntervalSeconds: number }>;
  };
  discordSettings: {
    meetingAutoAfkTimeoutSeconds: number;
    meetingSummariesEnabled: boolean;
    meetingSummaryMinParticipants: number;
    meetingSummaryMinDurationSeconds: number;
    meetingSummaryLanguage: string;
    meetingSummaryRecipient: string;
    meetingAudioRetentionSeconds: number;
    meetingSummaryPrompt: string;
  };
  activitySummary: ActivitySummary;
};

export type ReportsPage = {
  reports: Report[];
  total: number;
  limit: number;
  offset: number;
  sources: string[];
};

export type ReportsPageCache = Record<string, ReportsPage>;

export type ServerStatsWarningLevel = "ok" | "warning" | "critical";

export type ServerStatsCategory = {
  key: string;
  label: string;
  path: string;
  bytes: number;
  exists: boolean;
};

export type ServerStats = {
  generatedAt: string;
  hostname: string;
  root: {
    path: string;
    totalBytes: number;
    usedBytes: number;
    freeBytes: number;
    usedPercent: number;
    warningLevel: ServerStatsWarningLevel;
  };
  categories: ServerStatsCategory[];
};

export type SiteUserRole = "admin" | "editor" | "viewer";

export type SiteUser = {
  email: string;
  displayName: string;
  role: SiteUserRole;
  active: boolean;
  /** Linked author profile avatar (GitHub-backed), when site email matches author_profiles. */
  avatarUrl?: string;
};

export type MeetingRecordingStatus = {
  recordingId: string;
  summaryId?: string;
  status: string;
  recordingStatus?: string;
  summaryStatus?: string;
  startedAt?: string;
  endedAt?: string;
  durationSeconds?: number;
  participantNames?: string[];
  participantCount?: number;
  audioFrameCount?: number;
  nonSilentFrameCount?: number;
  corruptedPacketCount?: number;
  unknownSourceFrameCount?: number;
  botFrameCount?: number;
  emptyPcmFrameCount?: number;
  silencePaddingFrameCount?: number;
  outOfOrderFrameCount?: number;
  mixedUserCount?: number;
  perUserFrameCounts?: Record<string, number>;
  perUserNonSilentFrameCounts?: Record<string, number>;
  listenErrorCount?: number;
  listenError?: string;
  audioQualityStatus?: string;
  audioSizeBytes?: number;
  recipient?: { kind?: string; label?: string };
  telegramSentAt?: string;
  error?: string;
  updatedAt?: string;
};

export type MeetingActivityItem =
  | {
      itemType: "day_separator";
      id: string;
      date: string;
      timestamp?: string;
    }
  | {
      itemType: "recording";
      id: string;
      date?: string;
      timestamp?: string;
      recording: MeetingRecordingStatus;
    }
  | {
      itemType: "voice_event";
      id: string;
      date?: string;
      timestamp?: string;
      eventType: string;
      rawAuthor?: string;
      discordUsername?: string;
      channelId?: string;
      afkChannelId?: string;
      meetingSeconds?: number;
    };

export type AnalyticsTotals = {
  daySeconds: number;
  activeSeconds: number;
  idleSeconds: number;
  meetingSeconds: number;
  overtimeActiveSeconds: number;
  breakSeconds: number;
  pluginDaySeconds: number;
  telegramDaySeconds: number;
  productivity: number;
};

export type AnalyticsAuthorSummary = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  authorColor?: string;
  avatarUrl?: string;
  months: AnalyticsMonth[];
};

export type AnalyticsSummary = {
  year: number;
  authors: AnalyticsAuthorSummary[];
};

export type AnalyticsMonth = {
  month: number;
  label: string;
  startDate: string;
  endDate: string;
  totals: AnalyticsTotals;
  previousMonthDeltas: AnalyticsDelta;
  weeks: AnalyticsWeek[];
};

export type AnalyticsWeek = {
  week: number;
  label: string;
  startDate: string;
  endDate: string;
  totals: AnalyticsTotals;
  previousWeekDeltas: AnalyticsDelta;
  days: AnalyticsDay[];
};

export type AnalyticsDay = {
  date: string;
  label: string;
  inMonth: boolean;
  totals: AnalyticsTotals;
  hourlyActivity: HourlyActivity[];
};

export type AnalyticsDelta = {
  activeSeconds: number;
  idleSeconds: number;
  meetingSeconds: number;
  overtimeActiveSeconds: number;
  breakSeconds: number;
  pluginDaySeconds: number;
  telegramDaySeconds: number;
  productivity: number;
};

export type CalendarAuthor = {
  rawAuthor: string;
  authorEmail?: string;
  displayName: string;
  team?: string;
  authorColor: string;
  avatarUrl?: string;
};

export type CalendarReason = {
  id: string;
  label: string;
};

export type CalendarMark = {
  rawAuthor: string;
  displayName: string;
  authorColor: string;
  date: string;
  reasonId: string;
  reasonLabel: string;
  note: string;
};

export type CalendarAuthorStats = {
  rawAuthor: string;
  displayName: string;
  authorColor: string;
  totalMarkedDays: number;
  byReason: Record<string, number>;
  latestMarks: CalendarMark[];
};

export type CalendarSummary = {
  year: number;
  authors: CalendarAuthor[];
  reasons: CalendarReason[];
  marks: CalendarMark[];
  stats: CalendarAuthorStats[];
};

export type DateRange = {
  startDate: string;
  endDate: string;
  preset: "live" | "yesterday" | "custom";
};

