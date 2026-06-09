import { useEffect, useState } from "react";
import { apiFetch, IS_LOCAL_DASHBOARD } from "../api/client";
import { AUTH_HINT_STORAGE_KEY } from "../constants/dashboard";
import {
  clearDashboardSessionCaches,
  readStoredSessionUserPreview,
  writeStoredSessionUserPreview
} from "../utils/dashboardStorage";
import type { SiteUser } from "../types/dashboard";

export function useAuthSession() {
  const [authUser, setAuthUser] = useState<SiteUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [hasAuthHint, setHasAuthHint] = useState(() => localStorage.getItem(AUTH_HINT_STORAGE_KEY) === "true");
  const [sessionUserPreview, setSessionUserPreview] = useState<SiteUser | null>(() => readStoredSessionUserPreview());

  useEffect(() => {
    async function loadAuth() {
      try {
        let response = await apiFetch("/api/v1/auth/me");

        if (!response.ok && IS_LOCAL_DASHBOARD) {
          response = await apiFetch("/api/v1/auth/dev-login", { method: "POST" });
        }

        if (response.ok) {
          const payload = await response.json();
          setAuthUser(payload.user);
          setHasAuthHint(true);
          localStorage.setItem(AUTH_HINT_STORAGE_KEY, "true");
        } else {
          clearAuthState();
        }
      } catch {
        setAuthUser(null);
      } finally {
        setAuthLoading(false);
      }
    }

    void loadAuth();
  }, []);

  useEffect(() => {
    if (authUser) {
      writeStoredSessionUserPreview(authUser);
      setSessionUserPreview(authUser);
    }
  }, [authUser]);

  function clearAuthState() {
    setAuthUser(null);
    setHasAuthHint(false);
    localStorage.removeItem(AUTH_HINT_STORAGE_KEY);
    setSessionUserPreview(null);
    writeStoredSessionUserPreview(null);
  }

  async function logout() {
    await apiFetch("/api/v1/auth/logout", { method: "POST" });
    clearAuthState();
    clearDashboardSessionCaches();
  }

  return {
    authUser,
    authLoading,
    hasAuthHint,
    sessionUserPreview,
    setAuthUser,
    clearAuthState,
    logout
  };
}
