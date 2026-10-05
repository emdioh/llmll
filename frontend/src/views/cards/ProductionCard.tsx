import { useState, type FormEvent } from "react";
import type { AnswerOut, SessionCard } from "../../api/client";
import UmlautField from "./UmlautField";

export default function ProductionCard({
  card,
  busy,
  result,
  onSubmit,
}: {
  card: SessionCard;
  busy: boolean;
  result: AnswerOut | null;
  onSubmit: (text: string, usedHint: boolean) => void;
}) {
  const [text, setText] = useState("");
  const [usedHint, setUsedHint] = useState(false);
  const locked = busy || result !== null;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!locked && text.trim()) onSubmit(text, usedHint);
  }

  return (
    <form onSubmit={submit}>
      <p className="tag">Say it in German</p>
      <p className="big">{card.prompt?.it}</p>
      {card.prompt?.needs_article && (
        <p className="note">Include the article.</p>
      )}
      {usedHint && card.hint && (
        <p className="note" role="status">
          Hint: {card.hint}
        </p>
      )}
      <UmlautField value={text} onChange={setText} locked={locked} />
      {!result && (
        <div className="row">
          {card.hint && (
            <button
              type="button"
              className="btn"
              disabled={busy || usedHint}
              onClick={() => setUsedHint(true)}
            >
              Hint
            </button>
          )}
          <button
            type="submit"
            className="btn primary"
            disabled={busy || !text.trim()}
          >
            Check
          </button>
        </div>
      )}
    </form>
  );
}
