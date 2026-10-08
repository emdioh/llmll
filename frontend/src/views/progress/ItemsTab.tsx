import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import {
  listItems,
  listProgressItems,
  type ItemSummary,
  type ItemKind,
  type MemoryState,
  type ProgressItemRow,
  type ProgressSort,
} from "../../api/client";
import { useApi } from "../../useApi";
import { formatDue, meanMastery, nextDue, STATUS_LABELS } from "../../format";
import MasteryBar from "../MasteryBar";
import { fmtDate, pct, STATE_LABELS, TAG_LABELS } from "./util";

type Sort = ProgressSort | "all";

const PAGE = 30;
const LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"];
const STATES = Object.keys(STATE_LABELS) as MemoryState[];
const SORT_LABELS: Record<Sort, string> = {
  all: "Everything (A–Z, incl. not yet met)",
  weakest: "Weakest first",
  strongest: "Strongest first",
  recent: "Recently practised",
  due: "Due soonest",
  errors: "Most errors",
};

function Row({ it }: { it: ProgressItemRow }) {
  const words = it.kind === "lemma";
  return (
    <li className="item-row">
      <Link
        className="row-link"
        to={`/progress/items/${encodeURIComponent(it.id)}`}
      >
        <span className="row-main">
          <strong lang={words ? "de" : undefined}>{it.label}</strong>
          <span className="muted">
            {words && it.translation_it ? `${it.translation_it} · ` : ""}
            {it.level} · {STATE_LABELS[it.state]}
            {it.last_practiced
              ? ` · practised ${fmtDate(it.last_practiced)}`
              : ""}
          </span>
          {it.errors > 0 && (
            <ul className="chips" aria-label="Top error tags">
              <li className="chip bad">
                {it.errors} {it.errors === 1 ? "error" : "errors"}
                {it.error_rate !== null ? ` (${pct(it.error_rate)})` : ""}
              </li>
              {it.top_tags.slice(0, 2).map((t) => (
                <li key={t.tag} className="chip">
                  {TAG_LABELS(t.tag)} ×{t.count}
                </li>
              ))}
            </ul>
          )}
        </span>
        <span className="row-meta">
          {words && it.facets.length > 1 ? (
            it.facets.map((f) => (
              <span key={f.facet} className="mini-bar">
                <span className="pct">{f.facet.slice(0, 3)}</span>
                <MasteryBar value={f.mastery} />
                <span className="pct">{pct(f.mastery)}</span>
              </span>
            ))
          ) : (
            <span className="mini-bar">
              <MasteryBar value={it.mastery} />
              <span className="pct">{pct(it.mastery)}</span>
            </span>
          )}
          <span className="muted">due {fmtDate(it.due)}</span>
        </span>
      </Link>
      {!words && (
        <Link
          className="mini-link"
          to={`/grammar/${encodeURIComponent(it.id)}`}
          aria-label={`Reference: ${it.label}`}
        >
          Ref.
        </Link>
      )}
    </li>
  );
}

/** A row of the full catalogue (also items the learner has not met, to opt in or out). */
function BrowseRow({ it }: { it: ItemSummary }) {
  return (
    <li className="item-row">
      <Link
        className="row-link"
        to={`/progress/items/${encodeURIComponent(it.id)}`}
      >
        <span className="row-main">
          <strong lang={it.kind === "lemma" ? "de" : undefined}>
            {it.label}
          </strong>
          <span className="muted">
            {it.kind === "lemma" && it.translation_it
              ? `${it.translation_it} · `
              : ""}
            {it.level}
          </span>
        </span>
        <span className="row-meta">
          <span className="status-pill">
            {it.status ? STATUS_LABELS[it.status] : "—"}
          </span>
          <span className="mini-bar">
            <MasteryBar value={meanMastery(it.memory)} />
          </span>
          <span className="muted">{formatDue(nextDue(it.memory))}</span>
        </span>
      </Link>
    </li>
  );
}

type Page =
  | { all: false; total: number; items: ProgressItemRow[] }
  | { all: true; total: number; items: ItemSummary[] };

/** Searchable, filterable, sortable list of progress rows for words or grammar. */
export default function ItemsTab({ mode }: { mode: "words" | "grammar" }) {
  const words = mode === "words";
  const [grammarKind, setGrammarKind] = useState<ItemKind>("grammar");
  const kind: ItemKind = words ? "lemma" : grammarKind;
  const [sort, setSort] = useState<Sort>(words ? "recent" : "weakest");
  const [level, setLevel] = useState("");
  const [state, setState] = useState<MemoryState | "">("");
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

  const key = JSON.stringify([kind, sort, level, state, q, offset]);
  const load = useCallback(
    async (): Promise<Page> => {
      if (sort === "all") {
        const r = await listItems({ kind, level, q, limit: PAGE, offset });
        return { all: true, total: r.total, items: r.items };
      }
      const r = await listProgressItems({
        kind,
        sort,
        level,
        state,
        q,
        limit: PAGE,
        offset,
      });
      return { all: false, total: r.total, items: r.items };
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );
  const result = useApi(key, load);
  const sorts: Sort[] = words
    ? ["recent", "weakest", "strongest", "due", "errors", "all"]
    : ["weakest", "errors", "recent", "due", "strongest", "all"];

  function reset<T>(set: (v: T) => void) {
    return (v: T) => {
      set(v);
      setOffset(0);
    };
  }

  return (
    <div>
      <div className="filters">
        {!words && (
          <label className="field">
            <span>Type</span>
            <select
              value={grammarKind}
              onChange={(e) =>
                reset(setGrammarKind)(e.target.value as ItemKind)
              }
            >
              <option value="grammar">Grammar points</option>
              <option value="construction">Constructions</option>
            </select>
          </label>
        )}
        <label className="field">
          <span>Sort</span>
          <select
            value={sort}
            onChange={(e) => reset(setSort)(e.target.value as Sort)}
          >
            {sorts.map((s) => (
              <option key={s} value={s}>
                {SORT_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Level</span>
          <select
            value={level}
            onChange={(e) => reset(setLevel)(e.target.value)}
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
          <span>State</span>
          <select
            value={state}
            disabled={sort === "all"}
            onChange={(e) =>
              reset(setState)(e.target.value as MemoryState | "")
            }
          >
            <option value="">All</option>
            {STATES.map((s) => (
              <option key={s} value={s}>
                {STATE_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        {words && (
          <label className="field grow">
            <span>Search</span>
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Word or translation"
            />
          </label>
        )}
      </div>
      {result.status === "loading" && <p role="status">Loading…</p>}
      {result.status === "error" && (
        <p role="alert">Could not load the list: {result.error.message}</p>
      )}
      {result.status === "ok" && (
        <>
          <p className="muted">
            {result.data.total} {result.data.total === 1 ? "item" : "items"}
          </p>
          <ul className="list">
            {result.data.all
              ? result.data.items.map((it) => <BrowseRow key={it.id} it={it} />)
              : result.data.items.map((it) => <Row key={it.id} it={it} />)}
          </ul>
          {result.data.items.length === 0 && (
            <p>Nothing here yet. Items appear after you practise them.</p>
          )}
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
              disabled={offset + PAGE >= result.data.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}
