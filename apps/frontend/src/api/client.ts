const LOCAL_HOSTNAMES = new Set(["127.0.0.1", "localhost"]);

export const IS_LOCAL_DASHBOARD = LOCAL_HOSTNAMES.has(window.location.hostname);
export const API_URL = import.meta.env.VITE_API_URL ?? (IS_LOCAL_DASHBOARD ? "http://127.0.0.1:8000" : "https://activity.mempic.com");
const SHOULD_LOG_API_TIMINGS = import.meta.env.DEV || import.meta.env.VITE_AL_API_TIMINGS === "true";

export async function apiFetch(path: string, init: RequestInit = {}) {
  const startedAt = performance.now();
  const response = await fetch(`${API_URL}${path}`, { ...init, credentials: "include" });

  if (SHOULD_LOG_API_TIMINGS) {
    const durationMs = Math.round((performance.now() - startedAt) * 10) / 10;
    const backendMs = response.headers.get("X-AL-Response-Time-Ms");
    const bytes = response.headers.get("X-AL-Response-Bytes") ?? response.headers.get("content-length") ?? "unknown";
    console.debug("[AL api]", path, `${durationMs}ms`, { backendMs, bytes, status: response.status });
  }

  return response;
}
