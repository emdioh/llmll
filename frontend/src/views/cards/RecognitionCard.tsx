import { useState } from "react";
import type { FlashcardAnswer, SessionCard } from "../../api/client";

export default function RecognitionCard({
  card,
  busy,
  result,
  onChoose,
}: {
  card: SessionCard;
  busy: boolean;
  result: FlashcardAnswer | null;
  onChoose: (choice: number) => void;
}) {
  const [chosen, setChosen] = useState<number | null>(null);
  const options = card.prompt?.options ?? [];
  return (
    <>
      <p className="tag">What does it mean?</p>
      <p className="big" lang="de">
        {card.prompt?.de}
      </p>
      <div className="options">
        {options.map((opt, i) => {
          const picked = chosen === i;
          const cls = [
            "btn",
            "option",
            picked && result ? `picked ${result.outcome}` : "",
            result?.expected?.correct_index === i ? "reveal" : "",
          ].join(" ");
          return (
            <button
              key={i}
              type="button"
              className={cls}
              disabled={busy || result !== null}
              aria-pressed={picked}
              data-correct={result?.expected?.correct_index === i || undefined}
              onClick={() => {
                setChosen(i);
                onChoose(i);
              }}
            >
              {opt}
            </button>
          );
        })}
      </div>
    </>
  );
}
