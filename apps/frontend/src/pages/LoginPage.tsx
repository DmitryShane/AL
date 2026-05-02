import { useState } from "react";
import type React from "react";
import { ShieldCheck } from "lucide-react";
import { apiFetch } from "../api/client";
import type { SiteUser } from "../types/dashboard";
export function LoginPage({ checkingSession = false, onLogin }: { checkingSession?: boolean; onLogin: (user: SiteUser) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const response = await apiFetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
      });

      if (!response.ok) {
        throw new Error(loginErrorMessage(response.status));
      }

      const payload = await response.json();
      onLogin(payload.user);
    } catch (requestError) {
      if (requestError instanceof Error && !isNetworkLoginError(requestError)) {
        setError(requestError.message);
      } else {
        setError(BACKEND_OFFLINE_MESSAGE);
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-hero">
        <img className="login-logo" src="/logo.png" alt="Mempic Game Studio" />
        <p className="eyebrow">Activity Logger</p>
        <h1>Welcome to the team ride control room.</h1>
        <p>
          Track Unity and Blender activity, spot stalled reports, and keep the production sprint moving from one focused dashboard.
        </p>
      </section>
      <form className="login-card" onSubmit={(event) => void submit(event)}>
        <div className="login-card-icon">
          <ShieldCheck size={28} />
        </div>
        <h2>Sign in</h2>
        <p>Use the email and password issued by your site administrator.</p>
        <label>
          Email
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" required />
        </label>
        <label>
          Password
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        {checkingSession ? <p className="notice">Checking session...</p> : null}
        {error ? <p className="notice error">{error}</p> : null}
        <button className="primary-button" type="submit" disabled={checkingSession || submitting}>
          {checkingSession ? "Checking..." : submitting ? "Signing in..." : "Enter dashboard"}
        </button>
      </form>
    </main>
  );
}

const BACKEND_OFFLINE_MESSAGE = "Backend is still offline after deploy. Please wait a moment and reload the page.";

function loginErrorMessage(status: number) {
  if (status === 401 || status === 403) {
    return "Invalid email or password";
  }

  if (status >= 500 || status === 0) {
    return BACKEND_OFFLINE_MESSAGE;
  }

  return "Login failed. Please try again.";
}

function isNetworkLoginError(error: Error) {
  return error instanceof TypeError || /failed to fetch|networkerror|load failed/i.test(error.message);
}

