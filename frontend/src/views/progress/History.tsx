import { useCallback, useState } from "react";
import { Link, useParams } from "react-router";
import {
  getProgressHistory,
  getProgressReading,
  getProgressSession,
} from "../../api/client";
import { useApi } from "../../useApi";
import AnswerCardView from "./AnswerCardView";
import { fmtDateTime, minutesLabel, pct } from "./util";

const PAGE = 20;

export function HistoryTab() {
  const [offset, setOffset] = useState(0);
  const load = useCallback(() => getProgressHistory(PAGE, offset), [offset]);
  const state = useApi(`history-${offset}`, load);
  if (state.status === "loading") return <p role="status">Loading…</p>;
  if (state.status === "error")
    return (
      <p role="alert">Could not load the history: {state.error.message}</p>
    );
  const { items, total } = state.data;
  return (
    <div>
      {items.length === 0 && <p>No lessons yet. Start a session!</p>}
      <ul className="list">
        {items.map((h) => (
          <li key={`${h.kind}-${h.id}`}>
            <Link
              className="row-link"
              to={`/progress/history/${h.kind}/${encodeURIComponent(h.id)}`}
            >
              <span className="row-main">
                <strong>
                  {h.kind === "reading"
                    ? `Reading: ${h.title ?? ""}`
                    : "Review session"}
                </strong>
                <span className="muted">
                  {fmtDateTime(h.started_at)} ·{" "}
                  {minutesLabel(h.duration_minutes)}
                </span>
              </span>
              <span className="row-meta">
                <span>
                  {h.cards_answered} {h.cards_answered === 1 ? "card" : "cards"}
                  {h.correct_rate !== null ? ` · ${pct(h.correct_rate)}` : ""}
                </span>
                <span className="muted">
                  {h.kind === "reading"
                    ? `${h.words_looked_up ?? 0} looked up`
                    : `${h.new_items} new`}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <div className="row pager">
        <button
          type="button"
          className="btn"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE))}
        >
          Previous
        </button>
        <button
          type="button"
          className="btn"
          disabled={offset + PAGE >= total}
          onClick={() => setOffset(offset + PAGE)}
        >
          Next
        </button>
      </div>
    </div>
  );
}

function Back() {
  return (
    <p className="back-nav">
      <Link to="/progress?tab=history">← History</Link>
    </p>
  );
}

export function SessionDetailView() {
  const { id = "" } = useParams();
  const load = useCallback(() => getProgressSession(id), [id]);
  const state = useApi(`session-${id}`, load);
  return (
    <section>
      <Back />
      {state.status === "loading" && <p role="status">Loading…</p>}
      {state.status === "error" && (
        <p role="alert">Could not load the session: {state.error.message}</p>
      )}
      {state.status === "ok" && (
        <>
          <h1>Review session</h1>
          <p className="muted">
            {fmtDateTime(state.data.started_at)} ·{" "}
            {minutesLabel(state.data.duration_minutes)} ·{" "}
            {state.data.cards_answered} cards
            {state.data.correct_rate !== null
              ? ` · ${pct(state.data.correct_rate)} correct`
              : ""}
            {` · ${state.data.new_items} new`}
          </p>
          <div className="list">
            {state.data.cards.map((c) => (
              <AnswerCardView key={c.attempt_id} card={c} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

export function ReadingDetailView() {
  const { id = "" } = useParams();
  const load = useCallback(() => getProgressReading(id), [id]);
  const state = useApi(`reading-${id}`, load);
  return (
    <section>
      <Back />
      {state.status === "loading" && <p role="status">Loading…</p>}
      {state.status === "error" && (
        <p role="alert">Could not load the reading: {state.error.message}</p>
      )}
      {state.status === "ok" && (
        <>
          <h1 lang="de">{state.data.title}</h1>
          <p className="muted">
            {state.data.level} · {fmtDateTime(state.data.started_at)} ·{" "}
            {minutesLabel(state.data.duration_minutes)}
            {state.data.source_title ? ` · ${state.data.source_title}` : ""}
          </p>
          <h2>Text</h2>
          <div className="reader-text" lang="de">
            {state.data.body.split(/\n{2,}/).map((p, i) => (
              <p key={i}>{p}</p>
            ))}
          </div>
          <h2>Words looked up</h2>
          {state.data.lookups.length === 0 ? (
            <p className="muted">None.</p>
          ) : (
            <ul className="glossary">
              {state.data.lookups.map((l, i) => (
                <li key={i} lang="de">
                  {l.item_id ? (
                    <Link
                      to={`/progress/items/${encodeURIComponent(l.item_id)}`}
                    >
                      {l.label ?? l.word}
                    </Link>
                  ) : (
                    (l.word ?? l.label)
                  )}
                </li>
              ))}
            </ul>
          )}
          <h2>Summary exercise</h2>
          {state.data.summary ? (
            <AnswerCardView card={state.data.summary} />
          ) : (
            <p className="muted">Not answered.</p>
          )}
        </>
      )}
    </section>
  );
}
