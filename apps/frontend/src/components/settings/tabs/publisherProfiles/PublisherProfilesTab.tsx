import { useEffect, useMemo, useState } from "react";
import { AuthorAvatar } from "../../../AuthorAvatar";
import type { DeviceProfile } from "../deviceProfiles/types";
import { linkPublisherDevice, loadPublisherProfiles, savePublisherProfile, unlinkPublisherDevice, uploadPublisherAvatar } from "./api";
import type { PublisherProfile } from "./types";
import "./PublisherProfilesTab.css";

type PublisherDraft = {
  displayName: string;
};

const emptyDraft: PublisherDraft = {
  displayName: ""
};

export function PublisherProfilesTab() {
  const [profiles, setProfiles] = useState<PublisherProfile[]>([]);
  const [devices, setDevices] = useState<DeviceProfile[]>([]);
  const [draft, setDraft] = useState<PublisherDraft>(emptyDraft);
  const [deviceDrafts, setDeviceDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    void reload();
  }, []);

  async function reload() {
    setLoading(true);
    setError("");

    try {
      const data = await loadPublisherProfiles();
      setProfiles(data.publisherProfiles);
      setDevices(data.deviceProfiles);
      setDeviceDrafts({});
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not load publisher profiles.");
    } finally {
      setLoading(false);
    }
  }

  async function handleSaveProfile() {
    const rawAuthor = draft.displayName.trim();

    if (!rawAuthor) {
      setError("Publisher name is required.");
      return;
    }

    setSaving(`profile:${rawAuthor}`);
    setError("");

    try {
      await savePublisherProfile(rawAuthor, {
        displayName: draft.displayName.trim(),
        team: "",
        authorColor: ""
      });
      setDraft(emptyDraft);
      await reload();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save publisher profile.");
    } finally {
      setSaving("");
    }
  }

  async function handleAvatarUpload(profile: PublisherProfile, file: File | undefined) {
    if (!file) {
      return;
    }

    setSaving(`avatar:${profile.rawAuthor}`);
    setError("");

    try {
      await uploadPublisherAvatar(profile.rawAuthor, file);
      await reload();
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Could not upload publisher avatar.");
    } finally {
      setSaving("");
    }
  }

  async function handleLinkDevice(profile: PublisherProfile) {
    const rawDevice = deviceDrafts[profile.rawAuthor] ?? "";

    if (!rawDevice) {
      return;
    }

    setSaving(`device:${profile.rawAuthor}`);
    setError("");

    try {
      await linkPublisherDevice(profile.rawAuthor, rawDevice);
      await reload();
    } catch (linkError) {
      setError(linkError instanceof Error ? linkError.message : "Could not link device.");
    } finally {
      setSaving("");
    }
  }

  async function handleUnlinkDevice(profile: PublisherProfile, rawDevice: string) {
    setSaving(`device:${profile.rawAuthor}:${rawDevice}`);
    setError("");

    try {
      await unlinkPublisherDevice(profile.rawAuthor, rawDevice);
      await reload();
    } catch (unlinkError) {
      setError(unlinkError instanceof Error ? unlinkError.message : "Could not unlink device.");
    } finally {
      setSaving("");
    }
  }

  const linkedDevices = useMemo(() => new Set(profiles.flatMap((profile) => profile.devices.map((device) => device.rawDevice))), [profiles]);
  const availableDevices = devices.filter((device) => !linkedDevices.has(device.rawDevice));

  return (
    <div className="panel">
      <div className="device-profiles-panel-head">
        <div>
          <h2>Publisher Profiles</h2>
          <p className="settings-caption">Device-only publisher or external tester profiles. Link auto-created device profiles to show their activity as one author.</p>
        </div>
      </div>
      {error ? <p className="settings-error">{error}</p> : null}
      <div className="profile-create-card publisher-profile-create-card">
        <label>
          Publisher Name
          <input
            value={draft.displayName}
            onChange={(event) => setDraft((item) => ({ ...item, displayName: event.target.value }))}
            placeholder="Publisher QA"
            autoComplete="off"
            data-1p-ignore
            data-lpignore="true"
          />
        </label>
        <button className="primary-button" type="button" disabled={loading || Boolean(saving)} onClick={() => void handleSaveProfile()}>
          {saving.startsWith("profile:") ? "Saving..." : "Create"}
        </button>
      </div>

      <div className="profile-table-shell publisher-profile-table-shell">
        <div className="profile-table publisher-profile-table">
          <div className="profile-table-head">
            <span>Publisher</span>
            <span>Avatar</span>
            <span>Linked Devices</span>
            <span>Add Device</span>
            <span>Actions</span>
          </div>
        {profiles.map((profile) => (
          <div className="profile-row" key={profile.rawAuthor}>
              <span className="profile-author-cell profile-author-cell--with-avatar" title={profile.rawAuthor}>
                <AuthorAvatar displayName={profile.displayName} authorColor={profile.authorColor} avatarUrl={profile.avatarUrl} />
                <span>
                  <strong>{profile.displayName}</strong>
                  {profile.rawAuthor !== profile.displayName || profile.team ? (
                    <small>{profile.rawAuthor !== profile.displayName ? profile.rawAuthor : ""}{profile.team ? ` · ${profile.team}` : ""}</small>
                  ) : null}
                </span>
              </span>
              <span>
                <input
                  className="publisher-profile-avatar-input"
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  disabled={Boolean(saving)}
                  onChange={(event) => void handleAvatarUpload(profile, event.target.files?.[0])}
                />
              </span>
              <span className="publisher-profile-linked-devices">
                {profile.devices.length ? profile.devices.map((device) => (
                  <button
                    className="publisher-profile-device-pill"
                    type="button"
                    disabled={Boolean(saving)}
                    title="Unlink device"
                    onClick={() => void handleUnlinkDevice(profile, device.rawDevice)}
                    key={device.rawDevice}
                  >
                    <strong>{device.rawDevice}</strong>
                    <small>{device.runtime || "Device"}</small>
                  </button>
                )) : <span className="profile-readonly-cell">No linked devices</span>}
              </span>
              <span className="publisher-profile-device-actions">
                <select
                  value={deviceDrafts[profile.rawAuthor] ?? ""}
                  onChange={(event) => setDeviceDrafts((items) => ({ ...items, [profile.rawAuthor]: event.target.value }))}
                >
                  <option value="">Select device</option>
                  {availableDevices.map((device) => (
                    <option value={device.rawDevice} key={device.rawDevice}>
                      {device.rawDevice}{device.runtime ? ` · ${device.runtime}` : ""}
                    </option>
                  ))}
                </select>
              </span>
              <span className="profile-actions">
                <button className="primary-button" type="button" disabled={!deviceDrafts[profile.rawAuthor] || Boolean(saving)} onClick={() => void handleLinkDevice(profile)}>
                  Link device
                </button>
              </span>
          </div>
        ))}
        </div>
        {!profiles.length ? <p className="profile-table-empty">{loading ? "Loading publisher profiles..." : "No publisher profiles yet."}</p> : null}
      </div>
    </div>
  );
}
