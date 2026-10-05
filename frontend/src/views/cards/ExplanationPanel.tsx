import { useEffect, useState } from "react";
import { explainEvaluation, type Explanation } from "../../api/client";
import Markdown from "../Markdown";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: Explanation }
  | { kind: "error"; message: string };

/** Explanation of one evaluated item; requested automatically or on demand. */
export default function ExplanationPanel({
  evaluationId,
  itemId,
  auto,
}: {
  evaluationId: number;
  itemId: string;
  auto: boolean;
}) {
  const [state, setState] = useState<State>(
    auto ? { kind: "loading" } : { kind: "idle" },
  );
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!auto && attempt === 0) return;
    let cancelled = false;
    explainEvaluation(evaluationId, itemId).then(
      (data) => {
        if (!cancelled) setState({ kind: "ok", data });
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
  }, [evaluationId, itemId, auto, attempt]);

  function request() {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  }

  if (state.kind === "idle")
    return (
      <button type="button" className="btn" onClick={request}>
        Spiegami meglio
      </button>
    );
  if (state.kind === "loading")
    return (
      <p role="status" className="muted">
        <span className="spinner" aria-hidden="true" />
        Preparing the explanation…
      </p>
    );
  if (state.kind === "error")
    return (
      <div>
        <p role="alert" className="error">
          Could not load the explanation: {state.message}
        </p>
        <button type="button" className="btn" onClick={request}>
          Try again
        </button>
      </div>
    );
  return (
    <div className="explanation">
      <Markdown>{state.data.markdown}</Markdown>
      <ExplanationExamples examples={state.data.examples} />
    </div>
  );
}

export function ExplanationExamples({
  examples,
}: {
  examples: Explanation["examples"];
}) {
  if (examples.length === 0) return null;
  return (
    <ul className="list">
      {examples.map((ex, i) => (
        <li key={i} className="row-static example">
          <span lang="de">{ex.de}</span>
          <em>{ex.translation}</em>
        </li>
      ))}
    </ul>
  );
}
