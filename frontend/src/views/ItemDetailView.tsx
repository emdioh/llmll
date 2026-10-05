import { useCallback, useState } from "react";
import { Link, useParams } from "react-router";
import { getItem, optinItem, optoutItem } from "../api/client";
import { formatDue, STATUS_LABELS } from "../format";
import { useApi } from "../useApi";
import MasteryBar from "./MasteryBar";

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean")
    return String(v);
  return JSON.stringify(v);
}

function OptControls({
  status,
  source,
  busy,
  error,
  onOptin,
  onOptout,
}: {
  status: string | null;
  source: string | null;
  busy: boolean;
  error: string | null;
  onOptin: () => void;
  onOptout: () => void;
}) {
  // Items that are already learned or presumed known cannot be opted in or out.
  if (status === "introduced" || status === "presumed_known") return null;
  const queuedByYou = status === "candidate" && source === "optin";
  return (
    <div className="row">
      {status === "suspended" && (
        <p className="muted">You asked not to be taught this.</p>
      )}
      {queuedByYou && <p className="muted">Queued: you chose to learn this.</p>}
      {!queuedByYou && (
        <button
          type="button"
          className="btn primary"
          disabled={busy}
          onClick={onOptin}
        >
          Learn this
        </button>
      )}
      {status !== "suspended" && (
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={onOptout}
        >
          Don&apos;t teach me this
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

export default function ItemDetailView() {
  const { id = "" } = useParams();
  const load = useCallback(() => getItem(id), [id]);
  const state = useApi(id, load);
  const [override, setOverride] = useState<{
    id: string;
    status: string;
    source: string | null;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [optError, setOptError] = useState<string | null>(null);

  async function opt(action: typeof optinItem) {
    setBusy(true);
    setOptError(null);
    try {
      const r = await action(id);
      setOverride({ id, status: r.status, source: r.candidate_source });
    } catch (err) {
      setOptError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const effective =
    override?.id === id
      ? { status: override.status, source: override.source }
      : {
          status: state.status === "ok" ? state.data.status : null,
          source: state.status === "ok" ? state.data.candidate_source : null,
        };

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
            ·{" "}
            {effective.status
              ? (STATUS_LABELS[effective.status] ?? effective.status)
              : "—"}
            {state.data.suspended ? " · suspended" : ""}
          </p>
          <OptControls
            status={effective.status}
            source={effective.source}
            busy={busy}
            error={optError}
            onOptin={() => opt(optinItem)}
            onOptout={() => opt(optoutItem)}
          />
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
