import { useLayoutEffect, useRef, useState, type FormEvent } from "react";
import type { AnswerOut, SessionCard } from "../../api/client";

const UMLAUTS = ["ä", "ö", "ü", "ß", "Ä", "Ö", "Ü"];

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
  const inputRef = useRef<HTMLInputElement>(null);
  const caretRef = useRef<number | null>(null);
  const locked = busy || result !== null;

  function insert(ch: string) {
    const el = inputRef.current;
    const start = el?.selectionStart ?? text.length;
    const end = el?.selectionEnd ?? text.length;
    caretRef.current = start + ch.length;
    setText(text.slice(0, start) + ch + text.slice(end));
  }

  // Restore the caret after React has re-rendered the controlled input, so a second
  // umlaut tapped right away lands in the right place.
  useLayoutEffect(() => {
    const el = inputRef.current;
    const caret = caretRef.current;
    if (el && caret !== null) {
      el.focus();
      el.setSelectionRange(caret, caret);
      caretRef.current = null;
    }
  }, [text]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!locked && text.trim()) onSubmit(text, usedHint);
  }

  return (
    <form onSubmit={submit}>
      <p className="tag">Say it in German</p>
      <p className="big">{card.prompt.it}</p>
      {card.prompt.needs_article && (
        <p className="note">Include the article.</p>
      )}
      {usedHint && card.hint && (
        <p className="note" role="status">
          Hint: {card.hint}
        </p>
      )}
      <div className="umlauts">
        {UMLAUTS.map((ch) => (
          <button
            key={ch}
            type="button"
            className="btn key"
            disabled={locked}
            onClick={() => insert(ch)}
            aria-label={`Insert ${ch}`}
          >
            {ch}
          </button>
        ))}
      </div>
      <input
        ref={inputRef}
        className="text-input"
        type="text"
        aria-label="Your answer"
        lang="de"
        autoCapitalize="off"
        autoCorrect="off"
        autoComplete="off"
        spellCheck={false}
        value={text}
        readOnly={locked}
        onChange={(e) => setText(e.target.value)}
      />
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
