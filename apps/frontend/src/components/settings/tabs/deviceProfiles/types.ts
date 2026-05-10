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
  createdAt?: string;
  lastSeenAt?: string;
};

export type DeviceProfileAuthorOption = {
  rawAuthor: string;
  displayName: string;
};
