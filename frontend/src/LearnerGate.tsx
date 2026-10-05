import { useEffect, useState, type ReactNode } from "react";
import { ApiError, getLearner, type Learner } from "./api/client";
import { LearnerContext } from "./learnerContext";
import Onboarding from "./views/Onboarding";

type State =
  | { kind: "loading" }
  | { kind: "needs-setup" }
  | { kind: "error"; message: string }
  | { kind: "ready"; learner: Learner };

/** Loads the learner; shows onboarding when none exists yet (404). */
export default function LearnerGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getLearner().then(
      (learner) => {
        if (!cancelled) setState({ kind: "ready", learner });
      },
      (err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setState({ kind: "needs-setup" });
        } else {
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

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
  if (state.kind === "needs-setup") {
    return (
      <main className="content gate">
        <Onboarding
          onDone={(learner) => setState({ kind: "ready", learner })}
        />
      </main>
    );
  }
  return (
    <LearnerContext.Provider
      value={{
        learner: state.learner,
        setLearner: (learner) => setState({ kind: "ready", learner }),
      }}
    >
      {children}
    </LearnerContext.Provider>
  );
}
