import { useState, type ReactNode } from "react";
import type { ContestResult, ProductionAnswer } from "../../api/client";
import Markdown from "../Markdown";
import { contestMessage } from "../../format";
import { ContestForm } from "./Contest";
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
  // null: no form; [] : whole answer; otherwise the contested item.
  const [target, setTarget] = useState<string[] | null>(null);
  const [contest, setContest] = useState<ContestResult | null>(null);
  const contested = new Map(contest?.items.map((i) => [i.item_id, i.outcome]));
  const accepted = contest !== null && contest.contest.verdict !== "rejected";
  const wholeOk =
    accepted &&
    contest.contest.item_ids.length === 0 &&
    contest.items.every((i) => i.outcome === "correct");
  const shownOutcome = wholeOk ? "correct" : result.outcome;

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
    <div className={`feedback ${tone(shownOutcome)}`}>
      <p className="outcome" role="status">
        {OUTCOME_LABELS[shownOutcome]}
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
                <strong>{it.label}</strong>:{" "}
                {ITEM_LABELS[contested.get(it.item_id) ?? it.outcome]}
                {!contest && it.outcome !== "correct" && (
                  <>
                    {" "}
                    <button
                      type="button"
                      className="link"
                      aria-label={`Contest ${it.label}`}
                      onClick={() => setTarget([it.item_id])}
                    >
                      contest
                    </button>
                  </>
                )}
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
      <div className="contest">
        {contest ? (
          <p role="status">{contestMessage(contest)}</p>
        ) : target ? (
          <>
            {target.length > 0 && (
              <p className="muted">
                Contesting:{" "}
                {result.items
                  .filter((i) => target.includes(i.item_id))
                  .map((i) => i.label)
                  .join(", ")}
              </p>
            )}
            <ContestForm
              evaluationId={result.evaluation_id}
              itemIds={target}
              onResolved={(r) => {
                setContest(r);
                setTarget(null);
              }}
              onCancel={() => setTarget(null)}
            />
          </>
        ) : (
          result.outcome !== "correct" && (
            <button type="button" className="btn" onClick={() => setTarget([])}>
              Secondo me era giusto
            </button>
          )
        )}
      </div>
    </div>
  );
}
