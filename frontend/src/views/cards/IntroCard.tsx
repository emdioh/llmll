import type { SessionCard } from "../../api/client";

export default function IntroCard({
  card,
  busy,
  onDone,
}: {
  card: SessionCard;
  busy: boolean;
  onDone: () => void;
}) {
  const p = card.prompt;
  return (
    <>
      <p className="tag">New word</p>
      <p className="big" lang="de">
        {p.article ? `${p.article} ` : ""}
        {p.lemma ?? p.de}
      </p>
      {p.plural && (
        <p>
          Plural: <span lang="de">{p.plural}</span>
        </p>
      )}
      <p>
        {p.translation_it}
        {p.translation_en ? ` / ${p.translation_en}` : ""}
      </p>
      {p.example && (
        <p className="example">
          <span lang="de">{p.example.de}</span>
          <br />
          <em>{p.example.it}</em>
        </p>
      )}
      {p.interference_note && <p className="note">{p.interference_note}</p>}
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
