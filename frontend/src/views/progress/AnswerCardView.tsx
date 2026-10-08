import type {
  AnswerCard,
  FlashcardAnswer,
  ProductionAnswer,
} from "../../api/client";
import Feedback from "../cards/Feedback";
import ProductionFeedback from "../cards/ProductionFeedback";
import { fmtDateTime } from "./util";

const TYPE_LABELS: Record<AnswerCard["type"], string> = {
  flashcard_intro: "New word",
  flashcard_recognition: "Recognition",
  flashcard_production: "Recall",
  grammar_intro: "New grammar",
  production: "Written exercise",
};

const OUTCOME_LABELS = {
  correct: "Correct",
  assisted: "Almost",
  error: "Incorrect",
} as const;

const PRODUCTION_OUTCOME = {
  correct: "correct",
  assisted: "minor_errors",
  error: "major_errors",
} as const;

export function OutcomePill({
  outcome,
}: {
  outcome: keyof typeof OUTCOME_LABELS;
}) {
  return (
    <span className={`outcome-pill ${outcome}`}>{OUTCOME_LABELS[outcome]}</span>
  );
}

function ContestNote({ card }: { card: AnswerCard }) {
  const c = card.contest;
  if (!c) return null;
  const text =
    c.status === "open"
      ? "Contest pending"
      : c.verdict === "accepted"
        ? "Contested: accepted"
        : c.verdict === "partial"
          ? "Contested: partly accepted"
          : "Contested: rejected";
  return (
    <p className="muted">
      {text}
      {c.rationale ? ` — ${c.rationale}` : ""}
    </p>
  );
}

/**
 * One answered card, read-only. With `focusItem` (an item's recent answers) the outcome and
 * the highlighted errors are those of that item; otherwise those of the whole card.
 */
export default function AnswerCardView({
  card,
  focusItem = false,
}: {
  card: AnswerCard;
  focusItem?: boolean;
}) {
  const outcome =
    focusItem && card.item_outcome ? card.item_outcome : card.outcome;
  const errors = focusItem ? card.item_errors : card.errors;
  const intro =
    card.type === "flashcard_intro" || card.type === "grammar_intro";

  let body = null;
  if (card.type === "production") {
    const result: ProductionAnswer = {
      kind: "production",
      outcome: PRODUCTION_OUTCOME[outcome ?? "correct"],
      corrected_sentence: card.expected ?? "",
      errors,
      feedback: card.feedback ?? "",
      items: card.items.map((i) => ({ ...i, needs_remediation: false })),
      evaluation_id: card.evaluation_id ?? 0,
    };
    body = (
      <ProductionFeedback readOnly answer={card.answer ?? ""} result={result} />
    );
  } else if (!intro && outcome) {
    const result: FlashcardAnswer = {
      kind: "flashcard",
      outcome,
      expected: card.expected
        ? { text: card.expected, lemma: "", translation_it: "" }
        : null,
      diagnostic_tags: [],
      feedback_it: card.feedback ?? "",
      memory: null,
    };
    body = (
      <>
        <p>
          Your answer:{" "}
          <strong lang="de">{card.answer ?? <em>no answer</em>}</strong>
        </p>
        <Feedback readOnly result={result} />
      </>
    );
  }

  return (
    <article className="answer-card" data-testid="answer-card">
      <div className="meta">
        <span className="tag">{TYPE_LABELS[card.type]}</span>
        {focusItem && outcome && <OutcomePill outcome={outcome} />}
        <span className="muted">{fmtDateTime(card.answered_at)}</span>
        {card.used_hint && <span className="chip">hint used</span>}
      </div>
      {card.prompt && (
        <p lang={card.type === "production" ? undefined : "de"}>
          {card.prompt}
        </p>
      )}
      {card.instructions && <p className="muted">{card.instructions}</p>}
      {card.options && card.type === "flashcard_recognition" && (
        <ul className="opts" aria-label="Options">
          {card.options.map((o) => (
            <li key={o} className={o === card.answer ? "picked" : undefined}>
              {o}
            </li>
          ))}
        </ul>
      )}
      {intro && <p className="muted">Introduced (no answer expected).</p>}
      {body}
      <ContestNote card={card} />
    </article>
  );
}
