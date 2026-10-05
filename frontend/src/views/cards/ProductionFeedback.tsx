import { useState, type ReactNode } from "react";
import type { ProductionAnswer } from "../../api/client";
import Markdown from "../Markdown";
import ExplanationPanel from "./ExplanationPanel";

const OUTCOME_LABELS = {
  correct: "Correct",
  minor_errors: "Almost: minor errors",
  major_errors: "Needs work",
  off_task: "Off task",
} as const;

const ITEM_LABELS = {
  correct: "Correct",
  assisted: "Almost",
  error: "Incorrect",
} as const;

/** The tone class of the whole feedback box. */
function tone(outcome: ProductionAnswer["outcome"]): string {
  if (outcome === "correct") return "correct";
  if (outcome === "minor_errors") return "assisted";
  return "error";
}

export default function ProductionFeedback({
  answer,
  result,
}: {
  answer: string;
  result: ProductionAnswer;
}) {
  const [open, setOpen] = useState<number | null>(null);

  // Error spans are character offsets into the answer. Keep them ordered and non-overlapping,
  // and ignore any that fall outside the text.
  const spans = result.errors
    .map((e, index) => ({ e, index }))
    .filter(
      ({ e }) => e.start >= 0 && e.end > e.start && e.end <= answer.length,
    )
    .sort((a, b) => a.e.start - b.e.start);
  const nodes: ReactNode[] = [];
  let pos = 0;
  for (const { e, index } of spans) {
    if (e.start < pos) continue;
    if (e.start > pos) nodes.push(answer.slice(pos, e.start));
    nodes.push(
      <button
        key={index}
        type="button"
        className={`err ${e.severity}`}
        aria-expanded={open === index}
        onClick={() => setOpen(open === index ? null : index)}
      >
        {answer.slice(e.start, e.end)}
      </button>,
    );
    pos = e.end;
  }
  if (pos < answer.length) nodes.push(answer.slice(pos));

  const detail = open === null ? undefined : result.errors[open];

  return (
    <div className={`feedback ${tone(result.outcome)}`}>
      <p className="outcome" role="status">
        {OUTCOME_LABELS[result.outcome]}
      </p>
      <p className="tag">Your answer</p>
      <p className="answer-text" lang="de">
        {nodes}
      </p>
      {result.errors.length > 0 && !detail && (
        <p className="muted">Tap a highlighted part to see the correction.</p>
      )}
      {detail && (
        <div className="err-detail">
          <p>
            <span lang="de">{detail.original}</span>
            {" → "}
            <strong lang="de">{detail.correction}</strong>
          </p>
          <p>{detail.explanation}</p>
        </div>
      )}
      {result.corrected_sentence && (
        <>
          <p className="tag">Corrected</p>
          <p lang="de">
            <strong>{result.corrected_sentence}</strong>
          </p>
        </>
      )}
      {result.feedback && <Markdown>{result.feedback}</Markdown>}
      {result.items.length > 0 && (
        <ul className="items" aria-label="Items">
          {result.items.map((it) => (
            <li key={it.item_id}>
              <p>
                <strong>{it.label}</strong>: {ITEM_LABELS[it.outcome]}
              </p>
              <ExplanationPanel
                evaluationId={result.evaluation_id}
                itemId={it.item_id}
                auto={it.needs_remediation}
              />
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn" disabled title="Available soon">
        Secondo me era giusto
      </button>
    </div>
  );
}
