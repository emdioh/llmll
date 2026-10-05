import { useState } from "react";
import { Link } from "react-router";
import { getQueue } from "../api/client";
import { useLearner } from "../learnerContext";
import { useApi } from "../useApi";

/** Weekly budget summary and the "Coming up" list (GET /api/queue). */
export default function QueuePanel() {
  const { learner } = useLearner();
  const state = useApi("queue", getQueue);
  const [open, setOpen] = useState(false);
  if (state.status !== "ok") return null;
  const { budget, next } = state.data;
  const { weekly_new_lemmas: lemmas, weekly_new_grammar: grammar } =
    learner.settings;
  return (
    <div className="queue-summary">
      <p>
        This week: {budget.lemmas_left}/{lemmas} new words,{" "}
        {budget.grammar_left}/{grammar} grammar points left.
      </p>
      <p className="muted">
        Backlog: {budget.backlog} {budget.backlog === 1 ? "card" : "cards"} due
      </p>
      {next.length > 0 && (
        <>
          <button
            type="button"
            className="btn"
            aria-expanded={open}
            aria-controls="coming-up"
            onClick={() => setOpen(!open)}
          >
            Coming up ({next.length})
          </button>
          {open && (
            <ul id="coming-up" className="list" aria-label="Coming up">
              {next.map((n) => (
                <li key={n.item_id} className="row-static">
                  <Link to={`/corpus/${encodeURIComponent(n.item_id)}`}>
                    <span lang="de">{n.label}</span>
                  </Link>{" "}
                  <span className="badge" title="Why it is queued">
                    {n.source}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
