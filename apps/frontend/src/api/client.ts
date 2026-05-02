const LOCAL_HOSTNAMES = new Set(["127.0.0.1", "localhost"]);

export const IS_LOCAL_DASHBOARD = LOCAL_HOSTNAMES.has(window.location.hostname);
export const API_URL = import.meta.env.VITE_API_URL ?? (IS_LOCAL_DASHBOARD ? "http://127.0.0.1:8000" : "https://activity.mempic.com");

export function apiFetch(path: string, init: RequestInit = {}) {
  return fetch(`${API_URL}${path}`, { ...init, credentials: "include" });
}
