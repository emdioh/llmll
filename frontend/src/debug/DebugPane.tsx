import { useState } from "react";
import { useDebug } from "./debugContext";
import { useNow } from "./useNow";
import {
  extractPrompt,
  formatMs,
  formatTries,
  taskStats,
  totalLabel,
  timingSegments,
  tokensPerSecond,
  type DebugCall,
} from "./debugStore";

const COLLAPSED_CHARS = 700;
const COLLAPSED_LINES = 12;

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour12: false });
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn small"
      aria-label={label}
      onClick={() => {
        void (async () => {
          try {
            await navigator.clipboard.writeText(text);
            setDone(true);
            setTimeout(() => setDone(false), 1500);
          } catch {
            // clipboard unavailable (insecure context): nothing to do
          }
        })();
      }}
    >
      {done ? "Copied" : "Copy"}
    </button>
  );
}

function TextBlock({ title, text }: { title: string; text: string }) {
  const long =
    text.length > COLLAPSED_CHARS || text.split("\n").length > COLLAPSED_LINES;
  const [more, setMore] = useState(false);
  return (
    <section className="dbg-block">
      <div className="dbg-block-head">
        <h3>{title}</h3>
        <CopyButton text={text} label={`Copy ${title.toLowerCase()}`} />
      </div>
      <pre className={long && !more ? "dbg-pre clipped" : "dbg-pre"}>
        {text}
      </pre>
      {long && (
        <button
          type="button"
          className="link"
          aria-expanded={more}
          onClick={() => setMore(!more)}
        >
          {more ? "Show less" : "Show more"}
        </button>
      )}
    </section>
  );
}

function prettyJson(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function TimingBar({ call }: { call: DebugCall }) {
  const segments = timingSegments(call);
  if (!segments)
    return <p className="muted small">No timing breakdown for this call.</p>;
  const total = segments.reduce((s, x) => s + x.ms, 0) || 1;
  return (
    <div className="dbg-timing">
      <div className="dbg-bar" role="img" aria-label="Timing breakdown">
        {segments.map((s) => (
          <span
            key={s.key}
            className={`seg seg-${s.key}`}
            style={{ flexGrow: s.ms / total }}
            title={`${s.label} ${formatMs(s.ms)}`}
          />
        ))}
      </div>
      <ul className="dbg-legend">
        {segments.map((s) => (
          <li key={s.key}>
            <span className={`swatch seg-${s.key}`} aria-hidden="true" />
            {s.label} {formatMs(s.ms)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Details({ call }: { call: DebugCall }) {
  const { system, user } = extractPrompt(call.request);
  const hasPrompt = system !== null || user !== null;
  return (
    <div className="dbg-details">
      <TimingBar call={call} />
      {system !== null && <TextBlock title="System prompt" text={system} />}
      {user !== null && <TextBlock title="User content" text={user} />}
      {!hasPrompt && call.request !== null && (
        <TextBlock title="Request" text={prettyJson(call.request)} />
      )}
      {call.error !== null && <TextBlock title="Error" text={call.error} />}
      {call.response !== null && (
        <TextBlock title="Response" text={prettyJson(call.response)} />
      )}
      {call.state === "running" && (
        <p className="muted small">Waiting for the response…</p>
      )}
      <p className="muted small">
        prompt {call.promptVersion ?? "?"}
        {call.stopReason ? ` · stop ${call.stopReason}` : ""}
        {call.requestChars !== null ? ` · ${call.requestChars} chars` : ""}
        {call.dbId !== null ? ` · db #${call.dbId}` : ""}
      </p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | null }) {
  if (value === null) return null;
  return (
    <span className="chip">
      <span className="muted">{label}</span> {value}
    </span>
  );
}

function DebugRow({ call }: { call: DebugCall }) {
  const [open, setOpen] = useState(false);
  const now = useNow(call.state === "running");
  const tries = formatTries(call);
  const tps = tokensPerSecond(call);
  const tokens =
    call.inputTokens !== null || call.outputTokens !== null
      ? `${call.inputTokens ?? "–"} / ${call.outputTokens ?? "–"}` +
        (call.reasoningTokens !== null ? ` / ${call.reasoningTokens}` : "")
      : null;
  return (
    <li className={`dbg-row st-${call.state}`}>
      <button
        type="button"
        className="dbg-summary"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="dbg-line">
          <span className="muted">{clock(call.startedMs)}</span>
          <strong>{call.task}</strong>
          <span className={`badge st-${call.state}`}>{call.state}</span>
          <span className="dbg-total">{totalLabel(call, now)}</span>
        </span>
        <span className="dbg-line muted">
          {call.provider}/{call.model}
          {call.upstreamProvider ? ` via ${call.upstreamProvider}` : ""}
        </span>
        <span className="dbg-line">
          <Metric
            label="ttfb"
            value={call.ttfbMs !== null ? formatMs(call.ttfbMs) : null}
          />
          <Metric
            label="retry wait"
            value={call.retryWaitMs ? formatMs(call.retryWaitMs) : null}
          />
          <Metric label="" value={tries} />
          <Metric label="tok in/out/reasoning" value={tokens} />
          <Metric
            label=""
            value={tps !== null ? `${tps.toFixed(0)} tok/s` : null}
          />
        </span>
      </button>
      {open && <Details call={call} />}
    </li>
  );
}

export default function DebugPane({ variant }: { variant: "page" | "drawer" }) {
  const { calls, connection, paused, pendingCount, setPaused, clear } =
    useDebug();
  const [task, setTask] = useState("");
  const tasks = [...new Set(calls.map((c) => c.task))].sort();
  const shown = task ? calls.filter((c) => c.task === task) : calls;
  const stats = taskStats(shown);
  const connLabel = {
    connecting: "Connecting…",
    open: "Live",
    reconnecting: "Reconnecting…",
    closed: "Disconnected",
  }[connection];
  return (
    <div className={`dbg-pane ${variant}`}>
      <div className="dbg-controls">
        <span className={`conn conn-${connection}`} role="status">
          {connLabel}
        </span>
        <button
          type="button"
          className="btn small"
          aria-pressed={paused}
          onClick={() => setPaused(!paused)}
        >
          {paused
            ? `Resume${pendingCount ? ` (${pendingCount})` : ""}`
            : "Pause"}
        </button>
        <button type="button" className="btn small" onClick={clear}>
          Clear
        </button>
        <label className="dbg-filter">
          <span className="sr-only">Filter by task</span>
          <select value={task} onChange={(e) => setTask(e.target.value)}>
            <option value="">All tasks</option>
            {tasks.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      </div>
      {stats.length > 0 && (
        <ul className="dbg-stats" aria-label="Latency per task">
          {stats.map((s) => (
            <li key={s.task}>
              <strong>{s.task}</strong> p50 {formatMs(s.p50)} · p90{" "}
              {formatMs(s.p90)} · n={s.n}
            </li>
          ))}
        </ul>
      )}
      <div className="dbg-list-wrap">
        {shown.length === 0 ? (
          <p className="muted">
            {calls.length === 0
              ? "No LLM calls yet. Use the app and they appear here."
              : "No calls for this task."}
          </p>
        ) : (
          <ul className="dbg-list">
            {shown.map((c) => (
              <DebugRow key={c.key} call={c} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
