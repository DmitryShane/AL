import type { SettingsTab } from "../../types/dashboard";

export const SETTINGS_TABS: { key: SettingsTab; label: string }[] = [
  { key: "general", label: "General" },
  { key: "authors", label: "Author Profiles" },
  { key: "publisherProfiles", label: "Publisher Profiles" },
  { key: "deviceProfiles", label: "Device Profiles" },
  { key: "autoBreak", label: "Auto Break" },
  { key: "redirects", label: "Author Redirects" },
  { key: "discord", label: "Discord" },
  { key: "telegram", label: "Telegram" },
  { key: "meetingSummaries", label: "Meeting Summaries" },
  { key: "snapshots", label: "Activity Snapshots" },
  { key: "fakeOnline", label: "Fake Online" },
  { key: "users", label: "Site Users" }
];

export function isSettingsTab(value: string | null): value is SettingsTab {
  return SETTINGS_TABS.some((tab) => tab.key === value);
}
