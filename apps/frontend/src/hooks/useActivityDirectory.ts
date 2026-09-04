import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { clearDirectory, identities, readDirectory, saveDirectory, type ActivityAuthorIdentity } from "../utils/activityDirectory";

export function useActivityDirectory(email: string | undefined, authenticated: boolean, authors: ActivityAuthorIdentity[], ready: boolean, clearAuthState: () => void) {
  const [state, setState] = useState(() => ({ email, authors: readDirectory(email), ready: false }));
  const [error, setError] = useState<string | null>(null);
  const directory = state.email === email ? state.authors : readDirectory(email);
  useEffect(() => {
    if (state.email !== email) {
      clearDirectory();
      setError(null);
      setState({ email, authors: [], ready: false });
    }
  }, [email, state.email]);
  useEffect(() => {
    if (!authenticated || !email || !ready || !authors.length) return;
    const next = identities(authors);
    saveDirectory(email, next);
    setState({ email, authors: next, ready: true });
    setError(null);
  }, [email, authenticated, authors, ready]);
  useEffect(() => {
    if (!authenticated || !email || (ready && authors.length > 0) || directory.length) return;
    let cancelled = false;
    void apiFetch("/api/v1/activity/authors").then(async response => {
      if (cancelled) return;
      if (response.status === 401) { clearDirectory(); clearAuthState(); return; }
      if (!response.ok) throw new Error("Unable to load authors.");
      const payload = await response.json();
      if (cancelled) return;
      const next = identities(payload.authors);
      setError(null);
      saveDirectory(email, next);
      setState({ email, authors: next, ready: true });
    }).catch(() => { if (!cancelled) setError("Unable to load authors."); });
    return () => { cancelled = true; };
  }, [email, authenticated, ready, authors.length, directory.length]);
  return { directory, directoryReady: ready || (state.email === email && state.ready), directoryError: error };
}
