import { Link } from "react-router";
import { listGrammar, type GrammarSummary } from "../api/client";
import { useApi } from "../useApi";

function groupByLevel(items: GrammarSummary[]) {
  const groups = new Map<string, GrammarSummary[]>();
  for (const it of items) {
    groups.set(it.level, [...(groups.get(it.level) ?? []), it]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
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
                    {g.title_it}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
    </section>
  );
}
