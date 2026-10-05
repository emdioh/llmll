import { useCallback } from "react";
import { Link, useParams } from "react-router";
import { getGrammar } from "../api/client";
import { useApi } from "../useApi";
import Markdown from "./Markdown";

export default function GrammarDetailView() {
  const { id = "" } = useParams();
  const load = useCallback(() => getGrammar(id), [id]);
  const state = useApi(id, load);

  return (
    <section>
      <p>
        <Link to="/grammar">← Grammar</Link>
      </p>
      {state.status === "loading" && <p role="status">Loading…</p>}
      {state.status === "error" && (
        <p role="alert">Could not load the entry: {state.error.message}</p>
      )}
      {state.status === "ok" && (
        <>
          <h1>{state.data.title_it}</h1>
          <p className="muted">
            {state.data.title_en} · {state.data.level}
          </p>
          <Markdown>{state.data.reference_it}</Markdown>
          {state.data.examples.length > 0 && (
            <>
              <h2>Examples</h2>
              <ul className="list">
                {state.data.examples.map((ex, i) => (
                  <li key={i} className="row-static example">
                    <span lang="de">{ex.de}</span>
                    <em>{ex.it}</em>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}
