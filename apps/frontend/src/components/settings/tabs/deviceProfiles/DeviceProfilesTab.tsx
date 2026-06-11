import { useEffect, useState } from "react";
import { deleteAllDeviceProfiles, deleteDeviceProfile, loadDeviceProfileAuthorOptions, loadDeviceProfileChanges, loadDeviceProfiles, saveDeviceProfileAlias } from "./api";
import { DeviceProfileDeleteModal } from "./DeviceProfileDeleteModal";
import { DeviceProfilesBulkDeleteModal } from "./DeviceProfilesBulkDeleteModal";
import { DeviceProfilesTable } from "./DeviceProfilesTable";
import type { DeviceProfile, DeviceProfileAuthorOption } from "./types";
import "./DeviceProfilesTab.css";

let cachedDeviceProfiles: DeviceProfile[] | null = null;
let cachedAuthorOptions: DeviceProfileAuthorOption[] | null = null;
let cachedDeviceProfilesCursor: string | null = null;
let deviceProfilesLoadPromise: Promise<{ profiles: DeviceProfile[]; authors: DeviceProfileAuthorOption[] }> | null = null;
let deviceProfilesChangesPromise: Promise<DeviceProfile[]> | null = null;

function setCachedDeviceProfiles(profiles: DeviceProfile[]) {
  cachedDeviceProfiles = sortDeviceProfiles(profiles);
  cachedDeviceProfilesCursor = deviceProfilesCursor(cachedDeviceProfiles);
  return cachedDeviceProfiles;
}

function loadCachedDeviceProfileData() {
  if (cachedDeviceProfiles && cachedAuthorOptions) {
    return Promise.resolve({ profiles: cachedDeviceProfiles, authors: cachedAuthorOptions });
  }

  if (!deviceProfilesLoadPromise) {
    deviceProfilesLoadPromise = Promise.all([
      loadDeviceProfiles(),
      loadDeviceProfileAuthorOptions()
    ]).then(([profiles, authors]) => {
      setCachedDeviceProfiles(profiles);
      cachedAuthorOptions = authors;
      return { profiles: cachedDeviceProfiles ?? [], authors };
    }).finally(() => {
      deviceProfilesLoadPromise = null;
    });
  }

  return deviceProfilesLoadPromise;
}

function loadCachedDeviceProfileChanges() {
  if (!cachedDeviceProfilesCursor) {
    return Promise.resolve([]);
  }

  if (!deviceProfilesChangesPromise) {
    deviceProfilesChangesPromise = loadDeviceProfileChanges(cachedDeviceProfilesCursor)
      .then((changes) => {
        if (changes.length && cachedDeviceProfiles) {
          setCachedDeviceProfiles(mergeDeviceProfiles(cachedDeviceProfiles, changes));
        }

        return changes;
      })
      .finally(() => {
        deviceProfilesChangesPromise = null;
      });
  }

  return deviceProfilesChangesPromise;
}

function mergeDeviceProfiles(current: DeviceProfile[], changes: DeviceProfile[]) {
  const byKey = new Map(current.map((profile) => [deviceProfileKey(profile), profile]));

  for (const profile of changes) {
    byKey.set(deviceProfileKey(profile), profile);
  }

  return Array.from(byKey.values());
}

function sortDeviceProfiles(profiles: DeviceProfile[]) {
  return [...profiles].sort((left, right) => {
    const sourceCompare = (left.source ?? "").localeCompare(right.source ?? "");
    if (sourceCompare !== 0) {
      return sourceCompare;
    }

    return naturalDeviceKey(left.rawDevice).localeCompare(naturalDeviceKey(right.rawDevice));
  });
}

function naturalDeviceKey(value: string) {
  const match = /^(.*?)(\d+)$/.exec(value);
  if (!match) {
    return `${value.toLowerCase()}\u0000-1`;
  }

  return `${match[1].toLowerCase()}\u0000${match[2].padStart(12, "0")}`;
}

function deviceProfileKey(profile: DeviceProfile) {
  return `${profile.source ?? ""}:${profile.rawDevice}`;
}

function deviceProfilesCursor(profiles: DeviceProfile[]) {
  let latest = 0;

  for (const profile of profiles) {
    for (const value of [profile.lastSeenAt, profile.createdAt, profile.deviceLastSeenAt, profile.deviceCreatedAt]) {
      const timestamp = Date.parse(value ?? "");
      if (Number.isFinite(timestamp)) {
        latest = Math.max(latest, timestamp);
      }
    }
  }

  return latest > 0 ? new Date(latest).toISOString() : null;
}

export function DeviceProfilesTab() {
  const [deviceProfiles, setDeviceProfiles] = useState<DeviceProfile[]>([]);
  const [authorOptions, setAuthorOptions] = useState<DeviceProfileAuthorOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [savingRawDevice, setSavingRawDevice] = useState("");
  const [deletingRawDevice, setDeletingRawDevice] = useState("");
  const [aliasDrafts, setAliasDrafts] = useState<Record<string, string>>({});
  const [deleteTarget, setDeleteTarget] = useState<DeviceProfile | null>(null);
  const [deleteFailedRawDevice, setDeleteFailedRawDevice] = useState("");
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkDeleteFailed, setBulkDeleteFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (cachedDeviceProfiles && cachedAuthorOptions) {
        setDeviceProfiles(cachedDeviceProfiles);
        setAuthorOptions(cachedAuthorOptions);
        setAliasDrafts({});
        setLoading(false);
        setError("");

        try {
          await loadCachedDeviceProfileChanges();
          if (!cancelled && cachedDeviceProfiles) {
            setDeviceProfiles(cachedDeviceProfiles);
          }
        } catch (loadError) {
          if (!cancelled) {
            setError(loadError instanceof Error ? loadError.message : "Could not load device profile changes.");
          }
        }

        return;
      }

      setLoading(!cachedDeviceProfiles || !cachedAuthorOptions);
      setError("");

      try {
        const { profiles, authors } = await loadCachedDeviceProfileData();

        if (!cancelled) {
          setDeviceProfiles(profiles);
          setAuthorOptions(authors);
          setAliasDrafts({});
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Could not load device profiles.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  async function reloadDeviceProfiles() {
    const profiles = await loadDeviceProfiles();
    setDeviceProfiles(setCachedDeviceProfiles(profiles));
    setAliasDrafts({});
  }

  function handleLinkedAuthorDraftChange(rawDevice: string, targetRawAuthor: string) {
    setAliasDrafts((drafts) => ({ ...drafts, [rawDevice]: targetRawAuthor }));
  }

  async function handleLinkedAuthorSave(rawDevice: string) {
    const targetRawAuthor = aliasDrafts[rawDevice] ?? "";

    if (!targetRawAuthor) {
      return;
    }

    setSavingRawDevice(rawDevice);
    setError("");

    try {
      await saveDeviceProfileAlias(rawDevice, targetRawAuthor);
      await reloadDeviceProfiles();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save device profile alias.");
    } finally {
      setSavingRawDevice("");
    }
  }

  async function handleDeleteDeviceProfile() {
    if (!deleteTarget) {
      return;
    }

    setDeletingRawDevice(deleteTarget.rawDevice);
    setDeleteFailedRawDevice("");
    setError("");

    try {
      await deleteDeviceProfile(deleteTarget.rawDevice);
      await reloadDeviceProfiles();
      setDeleteTarget(null);
    } catch (deleteError) {
      setDeleteFailedRawDevice(deleteTarget.rawDevice);
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete device profile.");
    } finally {
      setDeletingRawDevice("");
    }
  }

  async function handleDeleteAllDeviceProfiles() {
    setBulkDeleting(true);
    setBulkDeleteFailed(false);
    setError("");

    try {
      await deleteAllDeviceProfiles();
      await reloadDeviceProfiles();
      setBulkDeleteOpen(false);
    } catch (deleteError) {
      setBulkDeleteFailed(true);
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete device profiles.");
    } finally {
      setBulkDeleting(false);
    }
  }

  return (
    <div className="panel" data-doc-target="settings-device-profiles-panel">
      <div className="device-profiles-panel-head">
        <div>
          <h2>Device Profiles</h2>
          <p className="settings-caption">
            Device identities created from Activity Logger device reports. Runtime distinguishes mobile devices from editor or desktop reports.
          </p>
        </div>
        <button
          className="primary-button danger-solid-button delete-all-data-solid-button"
          type="button"
          disabled={loading || bulkDeleting || deviceProfiles.length === 0}
          onClick={() => {
            setBulkDeleteFailed(false);
            setBulkDeleteOpen(true);
          }}
        >
          {bulkDeleting ? "Deleting..." : "Delete all devices"}
        </button>
      </div>
      {error ? <p className="settings-error">{error}</p> : null}
      <DeviceProfilesTable
        deviceProfiles={deviceProfiles}
        authorOptions={authorOptions}
        loading={loading}
        savingRawDevice={savingRawDevice}
        deletingRawDevice={deletingRawDevice}
        aliasDrafts={aliasDrafts}
        onLinkedAuthorDraftChange={handleLinkedAuthorDraftChange}
        onSaveLinkedAuthor={(rawDevice) => void handleLinkedAuthorSave(rawDevice)}
        onDeleteProfile={setDeleteTarget}
      />
      {deleteTarget ? (
        <DeviceProfileDeleteModal
          profile={deleteTarget}
          saving={deletingRawDevice === deleteTarget.rawDevice}
          deleteError={deleteFailedRawDevice === deleteTarget.rawDevice}
          onCancel={() => setDeleteTarget(null)}
          onDelete={() => void handleDeleteDeviceProfile()}
        />
      ) : null}
      {bulkDeleteOpen ? (
        <DeviceProfilesBulkDeleteModal
          deviceCount={deviceProfiles.length}
          saving={bulkDeleting}
          deleteError={bulkDeleteFailed}
          onCancel={() => setBulkDeleteOpen(false)}
          onDelete={() => void handleDeleteAllDeviceProfiles()}
        />
      ) : null}
    </div>
  );
}
