import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { listItems, type ItemKind, type ItemStatus } from "../api/client";
import { formatDue, meanMastery, nextDue, STATUS_LABELS } from "../format";
import { useApi } from "../useApi";
import MasteryBar from "./MasteryBar";

const PAGE = 50;
const KINDS: ItemKind[] = ["lemma", "grammar", "construction"];
const LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"];
const STATUSES = Object.keys(STATUS_LABELS) as ItemStatus[];

export default function CorpusView() {
  const [kind, setKind] = useState<ItemKind | "">("");
  const [level, setLevel] = useState("");
  const [status, setStatus] = useState<ItemStatus | "">("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => {
      setQ(search.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  const key = JSON.stringify([kind, level, status, q, offset]);
  const load = useCallback(
    () => listItems({ kind, level, status, q, limit: PAGE, offset }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );
  const state = useApi(key, load);

  function filter<T>(set: (v: T) => void) {
    return (v: T) => {
      set(v);
      setOffset(0);
    };
  }

  return (
    <section>
      <h1>Corpus</h1>
      <div className="filters">
        <label className="field">
          <span>Kind</span>
          <select
            value={kind}
            onChange={(e) => filter(setKind)(e.target.value as ItemKind | "")}
          >
            <option value="">All</option>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Level</span>
          <select
            value={level}
            onChange={(e) => filter(setLevel)(e.target.value)}
          >
            <option value="">All</option>
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Status</span>
          <select
            value={status}
            onChange={(e) =>
              filter(setStatus)(e.target.value as ItemStatus | "")
            }
          >
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="field grow">
          <span>Search</span>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Word or translation"
          />
        </label>
      </div>

      {state.status === "loading" && <p role="status">Loading…</p>}
      {state.status === "error" && (
        <p role="alert">Could not load the corpus: {state.error.message}</p>
      )}
      {state.status === "ok" && (
        <>
          <p className="muted">
            {state.data.total} {state.data.total === 1 ? "item" : "items"}
          </p>
          <ul className="list">
            {state.data.items.map((it) => (
              <li key={it.id}>
                <Link
                  className="row-link"
                  to={`/corpus/${encodeURIComponent(it.id)}`}
                >
                  <span className="row-main">
                    <strong lang="de">{it.label}</strong>
                    <span className="muted">
                      {it.translation_it ?? ""} · {it.kind} · {it.level}
                    </span>
                  </span>
                  <span className="row-meta">
                    <span className="status-pill">
                      {it.status ? STATUS_LABELS[it.status] : "—"}
                    </span>
                    <MasteryBar value={meanMastery(it.memory)} />
                    <span className="muted">
                      {formatDue(nextDue(it.memory))}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          {state.data.items.length === 0 && <p>No items match.</p>}
          <div className="row pager">
            <button
              type="button"
              className="btn"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              Previous
            </button>
            <button
              type="button"
              className="btn"
              disabled={offset + PAGE >= state.data.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next
            </button>
          </div>
        </>
      )}
    </section>
  );
}
