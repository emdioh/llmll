import { useCallback, useState } from "react";
import { Link, useParams } from "react-router";
import {
  ApiError,
  explainEvaluation,
  explainGrammar,
  getProgressItem,
  practiceItem,
  type Explanation,
  type PracticeResult,
  type ProgressItemDetail,
} from "../../api/client";
import { STATUS_LABELS } from "../../format";
import { useApi } from "../../useApi";
import { ExplanationExamples } from "../cards/ExplanationPanel";
import Markdown from "../Markdown";
import MasteryBar from "../MasteryBar";
import AnswerCardView from "./AnswerCardView";
import { TrajectoryChart } from "./charts";
import OptControls from "./OptControls";
import { fmtDate, pct, STATE_LABELS, TAG_LABELS } from "./util";

function ExplainBlock({ detail }: { detail: ProgressItemDetail }) {
  const { item } = detail;
  const [shown, setShown] = useState<Explanation | null>(
    detail.explanation
      ? {
          markdown: detail.explanation.markdown,
          examples: detail.explanation.examples,
          cached: true,
        }
      : null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Prefer the learner's own latest mistake on this item; grammar points can also be
  // explained without one.
  const evaluation = detail.recent_answers.find(
    (a) =>
      a.evaluation_id !== null &&
      a.item_outcome &&
      a.item_outcome !== "correct",
  )?.evaluation_id;
  const canExplain = evaluation != null || item.kind === "grammar";

  async function explain() {
    setBusy(true);
    setError(null);
    try {
      setShown(
        evaluation != null
          ? await explainEvaluation(evaluation, item.id)
          : await explainGrammar(
              item.id,
              "Spiegami questo punto di grammatica con parole semplici.",
            ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <button
        type="button"
        className="btn"
        disabled={busy || !canExplain}
        onClick={explain}
      >
        {busy ? "Preparing…" : shown ? "Spiegami di nuovo" : "Spiegami"}
      </button>
      {!canExplain && (
        <p className="muted">
          An explanation is available once you have made a mistake on this item.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {shown && (
        <div className="explanation">
          <Markdown>{shown.markdown}</Markdown>
          <ExplanationExamples examples={shown.examples} />
        </div>
      )}
    </div>
  );
}

function practiceMessage(r: PracticeResult): string {
  if (r.production_slots === 0)
    return "Noted, but written exercises per session is 0 in Settings, so the queue is not used. Raise it to practise this item.";
  return r.already_queued
    ? "Already queued for your next session."
    : "Queued: your next session's written exercise will target this.";
}

function PracticeBlock({ id, queued }: { id: string; queued: boolean }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PracticeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function practice() {
    setBusy(true);
    setError(null);
    try {
      setResult(await practiceItem(id));
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "You have not met this item yet, so it cannot be practised. Use “Learn this” first."
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <button
        type="button"
        className="btn primary"
        disabled={busy || (queued && !result)}
        onClick={practice}
      >
        Practice this
      </button>
      {queued && !result && (
        <p className="muted" role="status">
          Already queued for your next session.
        </p>
      )}
      {result && <p role="status">{practiceMessage(result)}</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

function Detail({ detail }: { detail: ProgressItemDetail }) {
  const { item, counts } = detail;
  const words = item.kind === "lemma";
  const total = counts.correct + counts.assisted + counts.error;
  return (
    <>
      <p className="back-nav">
        <Link to={`/progress?tab=${words ? "words" : "grammar"}`}>
          ← Progress
        </Link>
      </p>
      <h1 lang={words ? "de" : undefined}>{item.label}</h1>
      <p>
        {words && item.translation_it ? `${item.translation_it} · ` : ""}
        {item.kind} · {item.level} · {STATE_LABELS[detail.state]}
        {item.status ? ` · ${STATUS_LABELS[item.status] ?? item.status}` : ""}
      </p>
      {!words && item.kind === "grammar" && (
        <p>
          <Link to={`/grammar/${encodeURIComponent(item.id)}`}>
            Open the grammar reference
          </Link>
        </p>
      )}
      <OptControls
        itemId={item.id}
        status={item.status}
        source={item.candidate_source}
      />

      <dl className="stat-grid">
        <div className="stat">
          <dt>Mastery</dt>
          <dd>
            {pct(detail.mastery)} <MasteryBar value={detail.mastery} />
          </dd>
        </div>
        <div className="stat">
          <dt>Answers</dt>
          <dd>
            {total}{" "}
            <small>
              {counts.correct} correct · {counts.assisted} almost ·{" "}
              {counts.error} wrong
            </small>
          </dd>
        </div>
        {detail.facets.map((f) => (
          <div className="stat" key={f.facet}>
            <dt>{f.facet}</dt>
            <dd>
              {pct(f.mastery)}{" "}
              <small>
                {STATE_LABELS[f.state]}
                {f.due ? ` · due ${fmtDate(f.due)}` : ""}
              </small>
            </dd>
          </div>
        ))}
      </dl>

      <div className="row">
        <PracticeBlock
          id={item.id}
          queued={detail.practice_queued}
          key={`p-${item.id}`}
        />
      </div>
      <ExplainBlock detail={detail} key={`e-${item.id}`} />

      <TrajectoryChart facets={detail.facets} />

      <h2>Typical errors</h2>
      {detail.tag_errors.length === 0 ? (
        <p className="muted">No errors recorded.</p>
      ) : (
        <ol className="list" aria-label="Error tags">
          {detail.tag_errors.map((t) => (
            <li key={t.tag} className="row-static">
              <span>{TAG_LABELS(t.tag)}</span>
              <strong>×{t.count}</strong>
            </li>
          ))}
        </ol>
      )}

      <h2>Recent answers</h2>
      {detail.recent_answers.length === 0 ? (
        <p className="muted">No answers yet.</p>
      ) : (
        <div className="list">
          {detail.recent_answers.map((a) => (
            <AnswerCardView key={a.attempt_id} card={a} focusItem />
          ))}
        </div>
      )}
      <details>
        <summary>Item data</summary>
        <dl className="kv">
          {Object.entries(item.payload).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{renderValue(v)}</dd>
            </div>
          ))}
        </dl>
        {item.requires.length > 0 && (
          <p className="muted">Requires: {item.requires.join(", ")}</p>
        )}
      </details>
    </>
  );
}

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean")
    return String(v);
  return JSON.stringify(v);
}

export default function ItemDetail() {
  const { id = "" } = useParams();
  const load = useCallback(() => getProgressItem(id), [id]);
  const state = useApi(id, load);
  return (
    <section>
      {state.status === "loading" && <p role="status">Loading…</p>}
      {state.status === "error" && (
        <>
          <p>
            <Link to="/progress">← Progress</Link>
          </p>
          <p role="alert">Could not load the item: {state.error.message}</p>
        </>
      )}
      {state.status === "ok" && <Detail detail={state.data} key={id} />}
    </section>
  );
}
