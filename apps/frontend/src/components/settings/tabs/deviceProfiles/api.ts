import { apiFetch } from "../../../../api/client";
import type { AuthorProfile } from "../../../../types/dashboard";
import type { DeviceProfile, DeviceProfileAuthorOption } from "./types";

export async function loadDeviceProfiles(): Promise<DeviceProfile[]> {
  const response = await apiFetch("/api/v1/authors/device-profiles");

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Could not load device profiles"));
  }

  const data = await response.json() as { deviceProfiles?: DeviceProfile[] };
  return Array.isArray(data.deviceProfiles) ? data.deviceProfiles : [];
}

export async function loadDeviceProfileAuthorOptions(): Promise<DeviceProfileAuthorOption[]> {
  const response = await apiFetch("/api/v1/authors/profiles");

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Could not load author profiles"));
  }

  const data = await response.json() as { profiles?: AuthorProfile[] };
  const profiles = data.profiles ?? [];

  return profiles.map((profile) => ({
    rawAuthor: profile.rawAuthor,
    displayName: profile.displayName || profile.rawAuthor
  }));
}

export async function saveDeviceProfileAlias(rawDevice: string, targetRawAuthor: string): Promise<void> {
  const response = await apiFetch(`/api/v1/authors/device-profiles/${encodeURIComponent(rawDevice)}/alias`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ targetRawAuthor })
  });

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Device profile alias save failed"));
  }
}

export async function deleteDeviceProfile(rawDevice: string): Promise<void> {
  const response = await apiFetch(`/api/v1/authors/device-profiles/${encodeURIComponent(rawDevice)}`, {
    method: "DELETE"
  });

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Device profile delete failed"));
  }
}

export async function deleteAllDeviceProfiles(): Promise<void> {
  const response = await apiFetch("/api/v1/authors/device-profiles", {
    method: "DELETE"
  });

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Device profiles delete failed"));
  }
}

async function apiErrorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json() as { detail?: unknown };
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
