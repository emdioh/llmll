import type { SessionCard } from "../../api/client";
import Markdown from "../Markdown";

export default function GrammarIntroCard({
  card,
  busy,
  onDone,
}: {
  card: SessionCard;
  busy: boolean;
  onDone: () => void;
}) {
  const p = card.prompt ?? {};
  return (
    <>
      <p className="tag">New grammar</p>
      <h2>{p.title}</h2>
      {p.reference_it && <Markdown>{p.reference_it}</Markdown>}
      {p.examples && p.examples.length > 0 && (
        <ul className="list">
          {p.examples.map((ex, i) => (
            <li key={i} className="row-static example">
              <span lang="de">{ex.de}</span>
              <em>{ex.it}</em>
            </li>
          ))}
        </ul>
      )}
      <button
        type="button"
        className="btn primary"
        disabled={busy}
        onClick={onDone}
      >
        Got it
      </button>
    </>
  );
}
