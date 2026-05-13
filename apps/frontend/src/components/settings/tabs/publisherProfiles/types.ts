import type { DeviceProfile } from "../deviceProfiles/types";

export type PublisherProfile = {
  rawAuthor: string;
  displayName: string;
  team?: string;
  profileType: "publisher";
  authorColor?: string;
  avatarUrl?: string;
  devices: DeviceProfile[];
};
