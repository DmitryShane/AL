import { useEffect, useState } from "react";
import { deleteAllDeviceProfiles, deleteDeviceProfile, loadDeviceProfileAuthorOptions, loadDeviceProfiles, saveDeviceProfileAlias } from "./api";
import { DeviceProfileDeleteModal } from "./DeviceProfileDeleteModal";
import { DeviceProfilesBulkDeleteModal } from "./DeviceProfilesBulkDeleteModal";
import { DeviceProfilesTable } from "./DeviceProfilesTable";
import type { DeviceProfile, DeviceProfileAuthorOption } from "./types";
import "./DeviceProfilesTab.css";

let cachedDeviceProfiles: DeviceProfile[] | null = null;
let cachedAuthorOptions: DeviceProfileAuthorOption[] | null = null;

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
        return;
      }

      setLoading(true);
      setError("");

      try {
        const [profiles, authors] = await Promise.all([
          loadDeviceProfiles(),
          loadDeviceProfileAuthorOptions()
        ]);

        if (!cancelled) {
          cachedDeviceProfiles = profiles;
          cachedAuthorOptions = authors;
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
    cachedDeviceProfiles = profiles;
    setDeviceProfiles(profiles);
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
    <div className="panel">
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
