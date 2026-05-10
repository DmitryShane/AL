export type DeviceProfile = {
  rawDevice: string;
  source?: string;
  runtime?: string;
  linkedAuthor?: string;
  linkedAuthorDisplayName?: string;
  idfa?: string;
  gaid?: string;
  projectId?: string;
  pluginVersion?: string;
  trackingAuthorizationStatus?: string;
  timeZoneId?: string;
  timeZoneDisplayName?: string;
  createdTimeZoneId?: string;
  createdTimeZoneDisplayName?: string;
  createdAt?: string;
  lastSeenAt?: string;
  deviceCreatedAt?: string;
  deviceLastSeenAt?: string;
};

export type DeviceProfileAuthorOption = {
  rawAuthor: string;
  displayName: string;
};
