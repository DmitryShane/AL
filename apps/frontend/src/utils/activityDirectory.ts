import type { AuthorRow } from "../types/dashboard";
import { localBrowserStorage, readStorageItem, removeStorageItem, writeStorageState } from "./browserStorage";

export type ActivityAuthorIdentity = Pick<AuthorRow, "rawAuthor" | "displayName" | "avatarUrl" | "team">;
const KEY = "AL.Activity.Directory.v1";
export function identities(authors: ActivityAuthorIdentity[]): ActivityAuthorIdentity[] {
  return authors.map(({ rawAuthor, displayName, avatarUrl, team }) => ({ rawAuthor, displayName, avatarUrl, team }));
}
export function directoryAccount(): string | undefined {
  try { return JSON.parse(readStorageItem(localBrowserStorage(), KEY) ?? "null")?.email; }
  catch { return undefined; }
}
export function readDirectory(email?: string): ActivityAuthorIdentity[] {
  try {
    const data = JSON.parse(readStorageItem(localBrowserStorage(), KEY) ?? "null");
    if (!email || data?.email !== email || !Array.isArray(data.authors)) return [];
    return identities(data.authors.filter((a: ActivityAuthorIdentity) => typeof a?.rawAuthor === "string" && typeof a.displayName === "string"));
  } catch { return []; }
}
export function saveDirectory(email: string, authors: ActivityAuthorIdentity[]) {
  writeStorageState(localBrowserStorage(), KEY, JSON.stringify({ email, authors: identities(authors) }));
}
export function clearDirectory() { removeStorageItem(localBrowserStorage(), KEY); }
