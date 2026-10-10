import { Link } from "react-router";
import { listGrammar, type GrammarSummary } from "../api/client";
import { useApi } from "../useApi";
import MasteryBar from "./MasteryBar";

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("it-IT", {
    day: "numeric",
    month: "short",
  });
}

function statusLabel(g: GrammarSummary): string | null {
  if (g.state === null || g.state === undefined) return null;
  if (g.state === "presumed_known" || g.status === "presumed_known")
    return "Già noto";
  const when = g.introduced_at ?? g.last_practiced;
  if (g.state === "new" && !when) return "Nuovo";
  return when ? `Studiato il ${shortDate(when)}` : "Studiato";
}

function groupByLevel(items: GrammarSummary[]) {
  const groups = new Map<string, GrammarSummary[]>();
  for (const it of items) {
    groups.set(it.level, [...(groups.get(it.level) ?? []), it]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function GrammarStatus({ g }: { g: GrammarSummary }) {
  const label = statusLabel(g);
  if (!label) return null;
  return (
    <small className="muted">
      <span className="grammar-status">{label}</span>
      {g.last_practiced && ` · ultimo esercizio ${shortDate(g.last_practiced)}`}
    </small>
  );
}

export default function GrammarView() {
  const state = useApi("grammar", listGrammar);
  return (
    <section>
      <h1>Grammar</h1>
      {state.status === "loading" && <p role="status">Loading…</p>}
      {state.status === "error" && (
        <p role="alert">Could not load grammar: {state.error.message}</p>
      )}
      {state.status === "ok" && state.data.length === 0 && (
        <p>No grammar points yet.</p>
      )}
      {state.status === "ok" &&
        groupByLevel(state.data).map(([level, items]) => (
          <div key={level}>
            <h2>{level}</h2>
            <ul className="list">
              {items.map((g) => (
                <li key={g.id}>
                  <Link
                    className="row-link"
                    to={`/grammar/${encodeURIComponent(g.id)}`}
                  >
                    <span className="grammar-main">
                      <span>{g.title_it}</span>
                      <GrammarStatus g={g} />
                    </span>
                    {g.mastery !== null && g.mastery !== undefined && (
                      <MasteryBar value={g.mastery} />
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
    </section>
  );
}
