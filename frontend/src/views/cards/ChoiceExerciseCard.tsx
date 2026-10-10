import { useState } from "react";
import type { SessionCard } from "../../api/client";

/** Multiple-choice gap fill: pick the option that completes the sentence. */
export default function ChoiceExerciseCard({
  card,
  busy,
  onChoose,
}: {
  card: SessionCard;
  busy: boolean;
  onChoose: (choice: number, option: string) => void;
}) {
  const [chosen, setChosen] = useState<number | null>(null);
  const options = card.prompt?.options ?? [];
  const sentence = (card.prompt?.text ?? "").replace("___", "＿＿＿");
  const newIds = new Set(card.item_ids ?? []);

  return (
    <>
      <p className="tag">Choose the right word</p>
      {card.instructions && <p>{card.instructions}</p>}
      <p className="big" lang="de">
        {sentence}
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
      <div className="options">
        {options.map((opt, i) => (
          <button
            key={i}
            type="button"
            className="btn option"
            lang="de"
            disabled={busy || chosen !== null}
            aria-pressed={chosen === i}
            onClick={() => {
              setChosen(i);
              onChoose(i, opt);
            }}
          >
            {opt}
          </button>
        ))}
      </div>
      {busy && (
        <p role="status" className="muted">
          <span className="spinner" aria-hidden="true" />
          Checking your answer…
        </p>
      )}
    </>
  );
}
