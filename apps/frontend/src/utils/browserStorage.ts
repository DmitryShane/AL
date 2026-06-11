type StorageKind = "local" | "session";

type StorageManifestEntry = {
  key: string;
  kind: "cache";
  size: number;
  storage: StorageKind;
  updatedAt: number;
};

type StorageManifest = {
  entries: StorageManifestEntry[];
  version: 1;
};

const STORAGE_MANIFEST_KEY = "AL.Dashboard.StorageManifest.v1";
const MAX_CACHE_ENTRIES = 20;
const MAX_CACHE_BYTES = 3 * 1024 * 1024;

const DASHBOARD_CACHE_PREFIXES = [
  "AL.Dashboard.Summary.",
  "AL.Dashboard.LastAuthors.",
  "AL.Dashboard.ActivitySummary.",
  "AL.Dashboard.ActivityHourly.",
  "AL.Dashboard.ActivityReports.",
  "AL.Dashboard.ActivityReports.v2.",
  "AL.Dashboard.AnalyticsSummary",
  "AL.Dashboard.CalendarSummary",
  "AL.Dashboard.ActivitySnapshots.Status."
];

const DASHBOARD_CACHE_KEYS = [
  "AL.Dashboard.SettingsSummary",
  "AL.Dashboard.DeviceProfiles",
  "al.serverStats.cache",
  "al.openAIStats.cache"
];

export function readStorageItem(storage: Storage | null, key: string) {
  if (!storage) {
    return null;
  }

  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

export function removeStorageItem(storage: Storage | null, key: string) {
  if (!storage) {
    return;
  }

  try {
    storage.removeItem(key);
  } catch {
    // Storage can be unavailable or blocked by the browser.
  }
  unregisterCacheEntry(storage, key);
}

export function writeStorageState(storage: Storage | null, key: string, value: string) {
  if (!storage) {
    return false;
  }

  try {
    storage.setItem(key, value);
    return true;
  } catch (error) {
    if (!isQuotaExceededError(error)) {
      return false;
    }
  }

  clearDashboardCaches();

  try {
    storage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

export function writeStorageCache(storage: Storage | null, key: string, value: string) {
  if (!storage) {
    return false;
  }

  const size = storageValueSize(value);

  if (size > MAX_CACHE_BYTES) {
    removeStorageItem(storage, key);
    return false;
  }

  evictDashboardCachesForWrite(storage, key, size);

  try {
    storage.setItem(key, value);
  } catch (error) {
    if (!isQuotaExceededError(error)) {
      return false;
    }

    evictDashboardCachesForWrite(storage, key, size, true);

    try {
      storage.setItem(key, value);
    } catch {
      return false;
    }
  }

  registerCacheEntry(storage, key, size);
  enforceDashboardCacheBudget(storage, key);
  return true;
}

export function clearDashboardCaches() {
  clearDashboardCacheStorage(localBrowserStorage());
  clearDashboardCacheStorage(sessionBrowserStorage());
}

export function localBrowserStorage() {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function sessionBrowserStorage() {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function isQuotaExceededError(error: unknown) {
  if (!error || typeof error !== "object") {
    return false;
  }

  const candidate = error as { code?: number; name?: string };
  return (
    candidate.name === "QuotaExceededError" ||
    candidate.name === "NS_ERROR_DOM_QUOTA_REACHED" ||
    candidate.code === 22 ||
    candidate.code === 1014
  );
}

function clearDashboardCacheStorage(storage: Storage | null) {
  if (!storage) {
    return;
  }

  for (const key of dashboardCacheKeysInStorage(storage)) {
    safeRemoveItem(storage, key);
  }
  removeManifestEntriesForStorage(storage);
}

function evictDashboardCachesForWrite(storage: Storage, reservedKey: string, incomingSize: number, clearAll = false) {
  const entries = dashboardCacheEntries(storage)
    .filter((entry) => entry.key !== reservedKey)
    .sort((left, right) => left.updatedAt - right.updatedAt || right.size - left.size);
  let totalSize = dashboardCacheTotalSize(storage);
  let totalEntries = dashboardCacheKeysInStorage(storage).filter((key) => key !== reservedKey).length + 1;

  for (const entry of entries) {
    const overBudget = totalEntries > MAX_CACHE_ENTRIES || totalSize + incomingSize > MAX_CACHE_BYTES;

    if (!clearAll && !overBudget) {
      break;
    }

    safeRemoveItem(storage, entry.key);
    unregisterCacheEntry(storage, entry.key);
    totalSize = Math.max(0, totalSize - entry.size);
    totalEntries = Math.max(1, totalEntries - 1);
  }
}

function enforceDashboardCacheBudget(storage: Storage, reservedKey: string) {
  const entries = dashboardCacheEntries(storage)
    .filter((entry) => entry.key !== reservedKey)
    .sort((left, right) => left.updatedAt - right.updatedAt || right.size - left.size);
  let totalSize = dashboardCacheTotalSize(storage);
  let totalEntries = dashboardCacheKeysInStorage(storage).length;

  for (const entry of entries) {
    if (totalEntries <= MAX_CACHE_ENTRIES && totalSize <= MAX_CACHE_BYTES) {
      break;
    }

    safeRemoveItem(storage, entry.key);
    unregisterCacheEntry(storage, entry.key);
    totalSize = Math.max(0, totalSize - entry.size);
    totalEntries = Math.max(0, totalEntries - 1);
  }
}

function dashboardCacheEntries(storage: Storage): StorageManifestEntry[] {
  const kind = storageKind(storage);
  const manifestEntries = readManifest().entries.filter((entry) => entry.storage === kind);
  const manifestKeys = new Set(manifestEntries.map((entry) => entry.key));
  const legacyEntries = dashboardCacheKeysInStorage(storage)
    .filter((key) => !manifestKeys.has(key))
    .map((key) => ({
      key,
      kind: "cache" as const,
      size: storageValueSize(readStorageItem(storage, key) ?? ""),
      storage: kind,
      updatedAt: 0
    }));

  return [...manifestEntries, ...legacyEntries].filter((entry) => readStorageItem(storage, entry.key) !== null);
}

function dashboardCacheKeysInStorage(storage: Storage) {
  const keys: string[] = [];

  try {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);

      if (key && isDashboardCacheKey(key)) {
        keys.push(key);
      }
    }
  } catch {
    return [];
  }

  return keys;
}

function dashboardCacheTotalSize(storage: Storage) {
  return dashboardCacheKeysInStorage(storage).reduce((total, key) => total + storageValueSize(readStorageItem(storage, key) ?? ""), 0);
}

function isDashboardCacheKey(key: string) {
  return key !== STORAGE_MANIFEST_KEY && (DASHBOARD_CACHE_KEYS.includes(key) || DASHBOARD_CACHE_PREFIXES.some((prefix) => key.startsWith(prefix)));
}

function registerCacheEntry(storage: Storage, key: string, size: number) {
  const manifest = readManifest();
  const kind = storageKind(storage);
  const entries = manifest.entries.filter((entry) => !(entry.storage === kind && entry.key === key));
  entries.push({ key, kind: "cache", size, storage: kind, updatedAt: Date.now() });
  writeManifest({ version: 1, entries });
}

function unregisterCacheEntry(storage: Storage, key: string) {
  const kind = storageKind(storage);
  const manifest = readManifest();
  const entries = manifest.entries.filter((entry) => !(entry.storage === kind && entry.key === key));

  if (entries.length !== manifest.entries.length) {
    writeManifest({ version: 1, entries });
  }
}

function removeManifestEntriesForStorage(storage: Storage) {
  const kind = storageKind(storage);
  const manifest = readManifest();
  const entries = manifest.entries.filter((entry) => entry.storage !== kind);
  writeManifest({ version: 1, entries });
}

function readManifest(): StorageManifest {
  const storage = localBrowserStorage();

  if (!storage) {
    return { version: 1, entries: [] };
  }

  try {
    const raw = storage.getItem(STORAGE_MANIFEST_KEY);
    const parsed = raw ? JSON.parse(raw) as Partial<StorageManifest> : null;

    if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.entries)) {
      return { version: 1, entries: [] };
    }

    return {
      version: 1,
      entries: parsed.entries.flatMap((entry) => {
        if (
          !entry ||
          typeof entry.key !== "string" ||
          entry.kind !== "cache" ||
          typeof entry.size !== "number" ||
          (entry.storage !== "local" && entry.storage !== "session") ||
          typeof entry.updatedAt !== "number"
        ) {
          return [];
        }

        return [entry as StorageManifestEntry];
      })
    };
  } catch {
    return { version: 1, entries: [] };
  }
}

function writeManifest(manifest: StorageManifest) {
  const storage = localBrowserStorage();

  if (!storage) {
    return;
  }

  try {
    storage.setItem(STORAGE_MANIFEST_KEY, JSON.stringify(manifest));
  } catch {
    // The manifest is an optimization; legacy cache scans still protect state writes.
  }
}

function storageKind(storage: Storage): StorageKind {
  return storage === sessionBrowserStorage() ? "session" : "local";
}

function safeRemoveItem(storage: Storage, key: string) {
  try {
    storage.removeItem(key);
  } catch {
    // Storage can be unavailable or blocked by the browser.
  }
}

function storageValueSize(value: string) {
  return value.length * 2;
}
