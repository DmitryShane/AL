import { apiFetch } from "../../../../api/client";
import type { DeviceProfile } from "../deviceProfiles/types";
import type { PublisherProfile } from "./types";

export async function loadPublisherProfiles(): Promise<{ publisherProfiles: PublisherProfile[]; deviceProfiles: DeviceProfile[] }> {
  const response = await apiFetch("/api/v1/publisher-profiles");

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Could not load publisher profiles"));
  }

  const data = await response.json() as { publisherProfiles?: PublisherProfile[]; deviceProfiles?: DeviceProfile[] };
  return {
    publisherProfiles: Array.isArray(data.publisherProfiles) ? data.publisherProfiles : [],
    deviceProfiles: Array.isArray(data.deviceProfiles) ? data.deviceProfiles : []
  };
}

export async function savePublisherProfile(rawAuthor: string, payload: { displayName: string; team?: string; authorColor?: string }): Promise<void> {
  const response = await apiFetch(`/api/v1/publisher-profiles/${encodeURIComponent(rawAuthor)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Publisher profile save failed"));
  }
}

export async function uploadPublisherAvatar(rawAuthor: string, file: File): Promise<void> {
  const form = new FormData();
  form.append("avatar", file);
  const response = await apiFetch(`/api/v1/publisher-profiles/${encodeURIComponent(rawAuthor)}/avatar`, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Publisher avatar upload failed"));
  }
}

export async function linkPublisherDevice(rawAuthor: string, rawDevice: string): Promise<void> {
  const response = await apiFetch(`/api/v1/publisher-profiles/${encodeURIComponent(rawAuthor)}/devices/${encodeURIComponent(rawDevice)}`, {
    method: "PUT"
  });

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Publisher device link failed"));
  }
}

export async function unlinkPublisherDevice(rawAuthor: string, rawDevice: string): Promise<void> {
  const response = await apiFetch(`/api/v1/publisher-profiles/${encodeURIComponent(rawAuthor)}/devices/${encodeURIComponent(rawDevice)}`, {
    method: "DELETE"
  });

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Publisher device unlink failed"));
  }
}

async function apiErrorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json() as { detail?: unknown };

    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  } catch {
    //
  }

  return `${fallback} (HTTP ${response.status})`;
}
