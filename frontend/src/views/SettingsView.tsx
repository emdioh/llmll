import { useEffect, useState } from "react";
import { getHealth } from "../api/client";
import type { HealthResponse } from "../api/types";

type State =
  | { kind: "loading" }
  | { kind: "ok"; health: HealthResponse }
  | { kind: "error"; message: string };

export default function SettingsView() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((health) => {
        if (!cancelled) setState({ kind: "ok", health });
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section>
      <h1>Settings</h1>
      <p>Preferences will live here; for now it shows the backend status.</p>
      <p className="status" role="status">
        {state.kind === "loading" && "Backend: checking…"}
        {state.kind === "ok" &&
          `Backend: ${state.health.status} (v${state.health.version}), database: ${state.health.database}`}
        {state.kind === "error" && `Backend: unreachable (${state.message})`}
      </p>
    </section>
  );
}
