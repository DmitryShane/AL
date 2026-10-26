import { apiFetch } from "../../../../api/client";
import type { DeviceProfile } from "../deviceProfiles/types";
import type { PublisherProfile, PublisherAuthor, PublisherAuthorJob } from "./types";

export async function loadPublisherProfiles(): Promise<{ publisherProfiles: PublisherProfile[]; deviceProfiles: DeviceProfile[]; authorProfiles: PublisherAuthor[]; authorLinkJob: PublisherAuthorJob | null }> {
  const response = await apiFetch("/api/v1/publisher-profiles");

  if (!response.ok) {
    throw new Error(await apiErrorDetail(response, "Could not load publisher profiles"));
  }

  const data = await response.json() as { publisherProfiles?: PublisherProfile[]; deviceProfiles?: DeviceProfile[]; authorProfiles?: PublisherAuthor[]; authorLinkJob?: PublisherAuthorJob };
  return {
    publisherProfiles: Array.isArray(data.publisherProfiles) ? data.publisherProfiles : [],
    deviceProfiles: Array.isArray(data.deviceProfiles) ? data.deviceProfiles : [],
    authorProfiles: data.authorProfiles ?? [],
    authorLinkJob: data.authorLinkJob ?? null
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

export async function assignPublisherAuthor(publisher: string, author: string, linked: boolean): Promise<string> {
  const response = await apiFetch(`/api/v1/publisher-profiles/${encodeURIComponent(publisher)}/authors/${encodeURIComponent(author)}`, { method: linked ? "PUT" : "DELETE" });
  if (!response.ok) throw new Error(await apiErrorDetail(response, "Could not update publisher author"));
  return (await response.json() as { jobId: string }).jobId;
}

export async function publisherAuthorJob(jobId: string): Promise<PublisherAuthorJob | null> {
  const response = await apiFetch(`/api/v1/authors/activity/rebuild/status?jobId=${encodeURIComponent(jobId)}`);
  if (!response.ok) throw new Error(await apiErrorDetail(response, "Could not load rebuild progress"));
  return (await response.json() as { job: PublisherAuthorJob | null }).job;
}

export async function retryPublisherAuthorJob(jobId: string): Promise<string> {
  const response = await apiFetch(`/api/v1/publisher-profiles/author-jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
  if (!response.ok) throw new Error(await apiErrorDetail(response, "Could not retry publisher author update"));
  return (await response.json() as { jobId: string }).jobId;
}
