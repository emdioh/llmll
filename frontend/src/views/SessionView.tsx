import { useEffect, useRef, useState } from "react";
import {
  createSession,
  submitAnswer,
  type AnswerOut,
  type SessionCard,
} from "../api/client";
import ProductionCard from "./cards/ProductionCard";
import RecognitionCard from "./cards/RecognitionCard";
import IntroCard from "./cards/IntroCard";
import Feedback from "./cards/Feedback";

interface Stats {
  reviewed: number;
  added: number;
  errors: number;
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "running"; sessionId: string; cards: SessionCard[]; index: number }
  | { kind: "summary"; stats: Stats; total: number };

const ZERO: Stats = { reviewed: 0, added: 0, errors: 0 };

export default function SessionView() {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [stats, setStats] = useState<Stats>(ZERO);
  const [result, setResult] = useState<AnswerOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const startedAt = useRef(0);
  const index = state.kind === "running" ? state.index : -1;

  useEffect(() => {
    startedAt.current = performance.now();
  }, [index]);

  async function start() {
    setState({ kind: "loading" });
    setError(null);
    setStats(ZERO);
    setResult(null);
    try {
      const s = await createSession();
      setState({
        kind: "running",
        sessionId: s.session_id,
        cards: s.cards,
        index: 0,
      });
      if (s.cards.length === 0)
        setState({ kind: "summary", stats: ZERO, total: 0 });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState({ kind: "idle" });
    }
  }

  function advance(nextStats: Stats) {
    if (state.kind !== "running") return;
    setResult(null);
    if (state.index + 1 >= state.cards.length) {
      setState({
        kind: "summary",
        stats: nextStats,
        total: state.cards.length,
      });
    } else {
      setState({ ...state, index: state.index + 1 });
    }
  }

  async function answer(
    card: SessionCard,
    payload: { choice?: number; text?: string },
    usedHint: boolean,
  ) {
    if (state.kind !== "running" || busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await submitAnswer(state.sessionId, {
        exercise_id: card.exercise_id,
        answer: payload,
        used_hint: usedHint,
        duration_ms: Math.round(performance.now() - startedAt.current),
      });
      const next: Stats =
        card.type === "flashcard_intro"
          ? { ...stats, added: stats.added + 1 }
          : {
              ...stats,
              reviewed: stats.reviewed + 1,
              errors: stats.errors + (out.outcome === "error" ? 1 : 0),
            };
      setStats(next);
      if (card.type === "flashcard_intro") advance(next);
      else setResult(out);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (state.kind === "idle" || state.kind === "loading") {
    return (
      <section>
        <h1>Session</h1>
        <p>A short review session: new words and cards that are due.</p>
        {error && <p role="alert">{error}</p>}
        <button
          type="button"
          className="btn primary"
          onClick={start}
          disabled={state.kind === "loading"}
        >
          {state.kind === "loading" ? "Preparing…" : "Start session"}
        </button>
      </section>
    );
  }

  if (state.kind === "summary") {
    return (
      <section>
        <h1>Session complete</h1>
        {state.total === 0 ? (
          <p>Nothing is due right now. Come back later!</p>
        ) : (
          <dl className="summary">
            <div>
              <dt>Reviewed</dt>
              <dd>{state.stats.reviewed}</dd>
            </div>
            <div>
              <dt>New</dt>
              <dd>{state.stats.added}</dd>
            </div>
            <div>
              <dt>Errors</dt>
              <dd>{state.stats.errors}</dd>
            </div>
          </dl>
        )}
        <button type="button" className="btn primary" onClick={start}>
          Start another session
        </button>
      </section>
    );
  }

  const card = state.cards[state.index];
  if (!card) return null;
  const last = state.index + 1 >= state.cards.length;

  return (
    <section>
      <p className="progress" aria-live="polite">
        Card {state.index + 1} of {state.cards.length}
      </p>
      <div className="card" key={card.exercise_id}>
        {card.type === "flashcard_intro" && (
          <IntroCard
            card={card}
            busy={busy}
            onDone={() => answer(card, {}, false)}
          />
        )}
        {card.type === "flashcard_recognition" && (
          <RecognitionCard
            card={card}
            busy={busy}
            result={result}
            onChoose={(choice) => answer(card, { choice }, false)}
          />
        )}
        {card.type === "flashcard_production" && (
          <ProductionCard
            card={card}
            busy={busy}
            result={result}
            onSubmit={(text, usedHint) => answer(card, { text }, usedHint)}
          />
        )}
        {error && <p role="alert">{error}</p>}
        {result && (
          <>
            <Feedback result={result} />
            <button
              type="button"
              className="btn primary"
              onClick={() => advance(stats)}
            >
              {last ? "Finish" : "Next"}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
