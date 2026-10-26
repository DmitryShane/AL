import type { DeviceProfile } from "../deviceProfiles/types";

export type PublisherProfile = {
  rawAuthor: string;
  displayName: string;
  team?: string;
  profileType: "publisher";
  authorColor?: string;
  avatarUrl?: string;
  devices: DeviceProfile[];
  authors: PublisherAuthor[];
};

export type PublisherAuthor = { rawAuthor: string; displayName: string };
export type PublisherAuthorJob = {
  jobId: string;
  status: "running" | "completed" | "failed";
  phase: string;
  progress: number;
  error?: string;
};
