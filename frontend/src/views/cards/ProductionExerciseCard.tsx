import { useState, type FormEvent } from "react";
import type { SessionCard } from "../../api/client";
import UmlautField from "./UmlautField";

export default function ProductionExerciseCard({
  card,
  busy,
  error,
  onSubmit,
}: {
  card: SessionCard;
  busy: boolean;
  /** Message of the last failed submission; the same text can be resubmitted. */
  error: string | null;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const newIds = new Set(card.item_ids ?? []);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!busy && text.trim()) onSubmit(text);
  }

  return (
    <form onSubmit={submit}>
      <p className="tag">Write in German</p>
      {card.instructions && <p>{card.instructions}</p>}
      <p className="big">{card.prompt?.text}</p>
      {card.glossary && card.glossary.length > 0 && (
        <ul className="glossary" aria-label="Glossary">
          {card.glossary.map((g) => (
            <li key={g.item_id} className={newIds.has(g.item_id) ? "new" : ""}>
              <span lang="de">{g.de}</span> = {g.translation}
            </li>
          ))}
        </ul>
      )}
      <UmlautField
        value={text}
        onChange={setText}
        locked={busy}
        multiline
        label="Your answer"
      />
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="row">
        <button
          type="submit"
          className="btn primary"
          disabled={busy || !text.trim()}
        >
          {busy ? "Checking…" : error ? "Try again" : "Check"}
        </button>
      </div>
      {busy && (
        <p role="status" className="muted">
          <span className="spinner" aria-hidden="true" />
          Grading your answer, this can take a few seconds…
        </p>
      )}
    </form>
  );
}
