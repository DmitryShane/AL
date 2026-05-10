import type { DeviceProfile } from "./types";

export function formatDeviceTracking(profile: DeviceProfile) {
  return profile.trackingAuthorizationStatus || "-";
}

export function formatDeviceDateTime(value?: string, timeZoneId?: string) {
  if (!value) {
    return "-";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  const options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  };

  if (timeZoneId) {
    options.timeZone = timeZoneId;
  }

  try {
    return date.toLocaleString(undefined, options);
  } catch {
    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  }
}
