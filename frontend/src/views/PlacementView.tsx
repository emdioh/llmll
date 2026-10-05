import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  ApiError,
  finishPlacement,
  getLearner,
  prepareExercise,
  startPlacement,
  submitAnswer,
  type AnswerOut,
  type PlacementFinish,
  type SessionCard,
} from "../api/client";
import { useLearner } from "../learnerContext";
import ProductionExerciseCard from "./cards/ProductionExerciseCard";
import ProductionFeedback from "./cards/ProductionFeedback";

type State =
  | { kind: "intro" }
  | { kind: "loading" }
  | { kind: "running"; id: string; cards: SessionCard[]; index: number }
  | { kind: "finishing"; id: string }
  | { kind: "result"; result: PlacementFinish };

/** Vocabulary card: "Lo so" / "Non lo so", then a 4-option translation check. */
function VocabCard({
  card,
  busy,
  onAnswer,
}: {
  card: SessionCard;
  busy: boolean;
  onAnswer: (payload: { choice?: number }) => void;
}) {
  const [checking, setChecking] = useState(false);
  const options = card.prompt?.options ?? [];
  return (
    <>
      <p className="tag">
        {checking ? "What does it mean?" : "Do you know this word?"}
      </p>
      <p className="big" lang="de">
        {card.prompt?.de}
      </p>
      {checking ? (
        <div className="options">
          {options.map((opt, i) => (
            <button
              key={i}
              type="button"
              className="btn option"
              disabled={busy}
              onClick={() => onAnswer({ choice: i })}
            >
              {opt}
            </button>
          ))}
        </div>
      ) : (
        <div className="row">
          <button
            type="button"
            className="btn primary"
            disabled={busy}
            onClick={() => setChecking(true)}
          >
            Lo so
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => onAnswer({})}
          >
            Non lo so
          </button>
        </div>
      )}
    </>
  );
}

/** Initial assessment (R§6): vocabulary cards, then guided grammar exercises. */
export default function PlacementView() {
  const { learner, setLearner } = useLearner();
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "intro" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnswerOut | null>(null);
  const [submitted, setSubmitted] = useState("");
  const finishCalled = useRef<string | null>(null);

  async function start() {
    setState({ kind: "loading" });
    setError(null);
    try {
      const p = await startPlacement();
      setState({
        kind: "running",
        id: p.placement_id,
        cards: p.cards,
        index: 0,
      });
      for (const c of p.cards)
        if (c.type === "production" && c.status === "pending")
          void prefetch(p.placement_id, c);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState({ kind: "intro" });
    }
  }

  async function prefetch(id: string, card: SessionCard) {
    let prepared: SessionCard;
    try {
      const { fallback_cards: _unused, ...rest } = await prepareExercise(
        card.exercise_id,
      );
      void _unused;
      prepared = rest;
    } catch {
      prepared = { ...card, status: "failed" };
    }
    setState((prev) =>
      prev.kind === "running" && prev.id === id
        ? {
            ...prev,
            cards: prev.cards.map((c) =>
              c.exercise_id === card.exercise_id ? prepared : c,
            ),
          }
        : prev,
    );
  }

  async function finish(id: string) {
    setError(null);
    try {
      const r = await finishPlacement(id);
      setState({ kind: "result", result: r });
      try {
        setLearner(await getLearner());
      } catch {
        // The level is refreshed on the next load anyway.
      }
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "This placement test is already finished."
          : err instanceof Error
            ? err.message
            : String(err),
      );
      setState({ kind: "intro" });
    }
  }

  const finishingId = state.kind === "finishing" ? state.id : null;
  useEffect(() => {
    if (finishingId === null || finishCalled.current === finishingId) return;
    finishCalled.current = finishingId;
    void finish(finishingId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishingId]);

  /** Functional update: prepare results may have landed since this render. */
  function advance() {
    setResult(null);
    setError(null);
    setState((prev) => {
      if (prev.kind !== "running") return prev;
      return prev.index + 1 >= prev.cards.length
        ? { kind: "finishing", id: prev.id }
        : { ...prev, index: prev.index + 1 };
    });
  }

  async function answer(
    card: SessionCard,
    payload: { choice?: number; text?: string },
  ) {
    if (state.kind !== "running" || busy) return;
    setBusy(true);
    setError(null);
    setSubmitted(payload.text ?? "");
    try {
      const out = await submitAnswer(state.id, {
        exercise_id: card.exercise_id,
        answer: payload,
        used_hint: false,
      });
      if (out.kind === "production") setResult(out);
      else advance();
    } catch (err) {
      setError(
        err instanceof ApiError && (err.status === 502 || err.status === 503)
          ? "Grading is unavailable right now. Your answer is kept: try again."
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setBusy(false);
    }
  }

  if (state.kind === "intro" || state.kind === "loading") {
    return (
      <section>
        <h1>Placement test</h1>
        <p>
          Quick placement test (≤5 min): some vocabulary and a few short writing
          exercises, so we can start at the right level (now {learner.level}).
        </p>
        {error && <p role="alert">{error}</p>}
        <div className="row">
          <button
            type="button"
            className="btn primary"
            disabled={state.kind === "loading"}
            onClick={start}
          >
            {state.kind === "loading" ? "Preparing…" : "Start the test"}
          </button>
          <button
            type="button"
            className="btn"
            disabled={state.kind === "loading"}
            onClick={() => navigate("/")}
          >
            Skip
          </button>
        </div>
      </section>
    );
  }

  if (state.kind === "finishing")
    return (
      <section>
        <p role="status">
          <span className="spinner" aria-hidden="true" />
          Estimating your level…
        </p>
      </section>
    );

  if (state.kind === "result") {
    const r = state.result;
    return (
      <section>
        <h1>Placement result</h1>
        <p>
          Estimated level: <strong>{r.estimated_level}</strong>
        </p>
        <p>
          {r.changed
            ? `Your level was updated from ${r.previous_level} to ${r.estimated_level}.`
            : `Your level stays ${r.previous_level}.`}
        </p>
        <p className="muted">{r.answered} answers counted.</p>
        <Link to="/" className="btn primary">
          Start learning
        </Link>
      </section>
    );
  }

  const card = state.cards[state.index];
  if (!card) return null;
  const last = state.index + 1 >= state.cards.length;

  return (
    <section>
      <p className="progress" aria-live="polite">
        {state.index + 1} / {state.cards.length}
      </p>
      <progress
        className="progress-bar"
        max={state.cards.length}
        value={state.index + 1}
        aria-label="Progress"
      />
      <div className="card" key={card.exercise_id}>
        {card.type === "flashcard_recognition" && (
          <VocabCard
            card={card}
            busy={busy}
            onAnswer={(p) => answer(card, p)}
          />
        )}
        {card.type === "production" && card.status === "pending" && (
          <p role="status">
            <span className="spinner" aria-hidden="true" />
            Preparing your exercise…
          </p>
        )}
        {card.type === "production" && card.status === "failed" && (
          <>
            <p role="alert">This exercise could not be prepared.</p>
            <button type="button" className="btn primary" onClick={advance}>
              {last ? "Finish" : "Skip"}
            </button>
          </>
        )}
        {card.type === "production" && card.status === "ready" && !result && (
          <ProductionExerciseCard
            card={card}
            busy={busy}
            error={error}
            onSubmit={(text) => answer(card, { text })}
          />
        )}
        {error && card.type !== "production" && <p role="alert">{error}</p>}
        {result?.kind === "production" && (
          <>
            <ProductionFeedback answer={submitted} result={result} />
            <button type="button" className="btn primary" onClick={advance}>
              {last ? "Finish" : "Next"}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
