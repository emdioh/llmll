import { useState } from "react";
import type { ContestResult, FlashcardAnswer } from "../../api/client";
import Markdown from "../Markdown";
import { contestMessage } from "../../format";
import { ContestForm } from "./Contest";

const LABELS = {
  correct: "Correct",
  assisted: "Almost",
  error: "Incorrect",
} as const;

export default function Feedback({ result }: { result: FlashcardAnswer }) {
  const { expected } = result;
  const [form, setForm] = useState(false);
  const [contest, setContest] = useState<ContestResult | null>(null);
  const outcome = contest?.items[0]?.outcome ?? result.outcome;
  if (!expected)
    return result.feedback_it ? (
      <div className="feedback correct" role="status">
        <Markdown>{result.feedback_it}</Markdown>
      </div>
    ) : null;
  return (
    <div className={`feedback ${outcome}`}>
      <p className="outcome" role="status">
        {LABELS[outcome]}
      </p>
      <p>
        Answer: <strong lang="de">{expected.text}</strong>
        {expected.translation_it ? ` (${expected.translation_it})` : ""}
      </p>
      {expected.plural && (
        <p>
          Plural: <span lang="de">{expected.plural}</span>
        </p>
      )}
      {result.feedback_it && <Markdown>{result.feedback_it}</Markdown>}
      {expected.example && (
        <p className="example">
          <span lang="de">{expected.example.de}</span>
          <br />
          <em>{expected.example.it}</em>
        </p>
      )}
      {result.evaluation_id != null && result.outcome !== "correct" && (
        <div className="contest">
          {contest ? (
            <p role="status">{contestMessage(contest)}</p>
          ) : form ? (
            <ContestForm
              evaluationId={result.evaluation_id}
              itemIds={[]}
              onResolved={setContest}
              onCancel={() => setForm(false)}
            />
          ) : (
            <button type="button" className="btn" onClick={() => setForm(true)}>
              Secondo me era giusto
            </button>
          )}
        </div>
      )}
    </div>
  );
}
