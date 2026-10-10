import { useRef, useState, type FormEvent } from "react";
import type { SessionCard } from "../../api/client";

const UMLAUTS = ["ä", "ö", "ü", "ß", "Ä", "Ö", "Ü"];

/** Gap fill: the learner types the missing word(s) inline in the sentence. */
export default function ClozeExerciseCard({
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
  const ref = useRef<HTMLInputElement>(null);
  const newIds = new Set(card.item_ids ?? []);
  const sentence = card.prompt?.text ?? "";
  const gap = sentence.indexOf("___");
  const before = gap >= 0 ? sentence.slice(0, gap) : sentence;
  const after = gap >= 0 ? sentence.slice(gap + 3) : "";

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!busy && text.trim()) onSubmit(text.trim());
  }

  function insert(ch: string) {
    const el = ref.current;
    const start = el?.selectionStart ?? text.length;
    const end = el?.selectionEnd ?? text.length;
    setText(text.slice(0, start) + ch + text.slice(end));
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + 1, start + 1);
    });
  }

  return (
    <form onSubmit={submit}>
      <p className="tag">Fill in the gap</p>
      {card.instructions && <p>{card.instructions}</p>}
      <p className="big" lang="de">
        {before}
        <input
          ref={ref}
          type="text"
          className="text-input"
          style={{ width: "8em", display: "inline-block" }}
          aria-label="Your answer"
          lang="de"
          autoCapitalize="off"
          autoCorrect="off"
          autoComplete="off"
          spellCheck={false}
          value={text}
          readOnly={busy}
          onChange={(e) => setText(e.target.value)}
        />
        {after}
      </p>
      {card.glossary && card.glossary.length > 0 && (
        <ul className="glossary" aria-label="Glossary">
          {card.glossary.map((g) => (
            <li key={g.item_id} className={newIds.has(g.item_id) ? "new" : ""}>
              <span lang="de">{g.de}</span> = {g.translation}
            </li>
          ))}
        </ul>
      )}
      <div className="umlauts">
        {UMLAUTS.map((ch) => (
          <button
            key={ch}
            type="button"
            className="btn key"
            disabled={busy}
            onClick={() => insert(ch)}
            aria-label={`Insert ${ch}`}
          >
            {ch}
          </button>
        ))}
      </div>
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
          Checking your answer…
        </p>
      )}
    </form>
  );
}
