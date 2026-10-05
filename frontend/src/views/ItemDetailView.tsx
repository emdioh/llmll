import { useCallback } from "react";
import { Link, useParams } from "react-router";
import { getItem } from "../api/client";
import { formatDue, STATUS_LABELS } from "../format";
import { useApi } from "../useApi";
import MasteryBar from "./MasteryBar";

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean")
    return String(v);
  return JSON.stringify(v);
}

export default function ItemDetailView() {
  const { id = "" } = useParams();
  const load = useCallback(() => getItem(id), [id]);
  const state = useApi(id, load);

  return (
    <section>
      <p>
        <Link to="/corpus">← Corpus</Link>
      </p>
      {state.status === "loading" && <p role="status">Loading…</p>}
      {state.status === "error" && (
        <p role="alert">Could not load the item: {state.error.message}</p>
      )}
      {state.status === "ok" && (
        <>
          <h1 lang="de">{state.data.label}</h1>
          <p>
            {state.data.translation_it} · {state.data.kind} · {state.data.level}{" "}
            · {state.data.status ? STATUS_LABELS[state.data.status] : "—"}
            {state.data.suspended ? " · suspended" : ""}
          </p>
          <h2>Memory</h2>
          {state.data.memory.length === 0 ? (
            <p className="muted">Not practised yet.</p>
          ) : (
            <ul className="list">
              {state.data.memory.map((m) => (
                <li key={m.facet} className="row-static">
                  <span>{m.facet}</span>
                  <MasteryBar value={m.mastery} />
                  <span className="muted">
                    due {formatDue(m.due ? new Date(m.due) : null)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <h2>Details</h2>
          <dl className="kv">
            {Object.entries(state.data.payload).map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{renderValue(v)}</dd>
              </div>
            ))}
            {Object.entries(state.data.interference).map(([k, v]) => (
              <div key={`i-${k}`}>
                <dt>interference: {k}</dt>
                <dd>{renderValue(v)}</dd>
              </div>
            ))}
          </dl>
          {state.data.requires.length > 0 && (
            <p className="muted">Requires: {state.data.requires.join(", ")}</p>
          )}
        </>
      )}
    </section>
  );
}
