import type { FlashcardAnswer } from "../../api/client";
import Markdown from "../Markdown";

const LABELS = {
  correct: "Correct",
  assisted: "Almost",
  error: "Incorrect",
} as const;

export default function Feedback({ result }: { result: FlashcardAnswer }) {
  const { expected } = result;
  if (!expected)
    return result.feedback_it ? (
      <div className="feedback correct" role="status">
        <Markdown>{result.feedback_it}</Markdown>
      </div>
    ) : null;
  return (
    <div className={`feedback ${result.outcome}`} role="status">
      <p className="outcome">{LABELS[result.outcome]}</p>
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
    </div>
  );
}
