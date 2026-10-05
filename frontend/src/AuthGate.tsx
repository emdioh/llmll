import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  ApiError,
  UNAUTHORIZED_EVENT,
  getAuthStatus,
  login,
  logout as apiLogout,
} from "./api/client";
import { AuthContext } from "./authContext";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "login" }
  | { kind: "ready"; enabled: boolean };

function LoginScreen({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || token.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      await login(token.trim());
      onLoggedIn();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Wrong access token"
          : err instanceof ApiError && err.status === 429
            ? "Too many failed attempts, try again in a few minutes"
            : err instanceof Error
              ? err.message
              : String(err),
      );
      setBusy(false);
    }
  }

  return (
    <main className="content gate">
      <h1>Sign in</h1>
      <form className="form" onSubmit={submit}>
        <label className="field">
          <span>Access token</span>
          <input
            type="password"
            autoComplete="current-password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

/** Checks `/api/auth/status` before anything else; shows a login screen when needed. */
export default function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getAuthStatus().then(
      (s) => {
        if (cancelled) return;
        setState(
          s.auth === "enabled" && !s.authenticated
            ? { kind: "login" }
            : { kind: "ready", enabled: s.auth === "enabled" },
        );
      },
      (err: unknown) => {
        if (!cancelled)
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // Any 401 from the API (expired session, rotated token) returns to the login screen.
  useEffect(() => {
    const onUnauthorized = () => setState({ kind: "login" });
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const logout = useCallback(() => {
    apiLogout().then(
      () => setState({ kind: "login" }),
      () => setState({ kind: "login" }),
    );
  }, []);

  if (state.kind === "loading") {
    return (
      <main className="content gate">
        <p role="status">Loading…</p>
      </main>
    );
  }
  if (state.kind === "error") {
    return (
      <main className="content gate">
        <p role="alert">Cannot reach the backend: {state.message}</p>
        <button
          type="button"
          className="btn"
          onClick={() => {
            setState({ kind: "loading" });
            setAttempt((n) => n + 1);
          }}
        >
          Retry
        </button>
      </main>
    );
  }
  if (state.kind === "login") {
    return (
      <LoginScreen
        onLoggedIn={() => {
          setState({ kind: "loading" });
          setAttempt((n) => n + 1);
        }}
      />
    );
  }
  return (
    <AuthContext.Provider value={{ enabled: state.enabled, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
