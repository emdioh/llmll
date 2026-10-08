/** Pure logic of the live LLM debug pane (design: docs/design/M7-observability.md §3). */
import type { DebugCallRow } from "../api/client";

export const MAX_CALLS = 200;
const MAX_PENDING = 400;

export type CallState = "running" | "ok" | "error";

/** One LLM call, assembled from `call_started` / `call_finished` or a history row. */
export interface DebugCall {
  /** Stable React key: the call id, or `db:<id>` for history rows. */
  key: string;
  uid: string | null;
  dbId: number | null;
  state: CallState;
  startedMs: number;
  finishedMs: number | null;
  task: string;
  provider: string;
  model: string;
  promptVersion: string | null;
  request: unknown;
  requestChars: number | null;
  response: unknown;
  error: string | null;
  stopReason: string | null;
  latencyMs: number | null;
  attempts: number | null;
  httpStatuses: (number | null)[] | null;
  retryWaitMs: number | null;
  ttfbMs: number | null;
  downloadMs: number | null;
  overheadMs: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
  reasoningTokens: number | null;
  upstreamProvider: string | null;
}

export type EventName = "call_started" | "call_finished";
export interface LiveMessage {
  name: EventName;
  data: Record<string, unknown>;
}

export interface DebugState {
  calls: DebugCall[];
  paused: boolean;
  /** Events received while paused, applied on resume. */
  pending: LiveMessage[];
  /** Call ids hidden by "Clear" (a reconnect resends the server buffer). */
  ignored: string[];
}

export const initialState: DebugState = {
  calls: [],
  paused: false,
  pending: [],
  ignored: [],
};

export type Action =
  | { type: "event"; message: LiveMessage }
  | { type: "history"; rows: DebugCallRow[] }
  | { type: "clear" }
  | { type: "pause"; paused: boolean };

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const int = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const time = (v: unknown): number | null => {
  const t = typeof v === "string" ? Date.parse(v) : NaN;
  return Number.isNaN(t) ? null : t;
};
const statuses = (v: unknown): (number | null)[] | null =>
  Array.isArray(v) ? v.map((s) => (typeof s === "number" ? s : null)) : null;

function finish(base: DebugCall, d: Record<string, unknown>): DebugCall {
  const finishedMs = time(d.ts) ?? Date.now();
  const latency = int(d.latency_ms);
  const startedMs =
    time(d.started_ts) ??
    (base.uid !== null || latency === null
      ? base.startedMs
      : finishedMs - latency);
  const error = str(d.error);
  return {
    ...base,
    dbId: int(d.db_id),
    state: error ? "error" : "ok",
    startedMs,
    finishedMs,
    task: str(d.task) ?? base.task,
    provider: str(d.provider) ?? base.provider,
    model: str(d.model) ?? base.model,
    promptVersion: str(d.prompt_version) ?? base.promptVersion,
    request: d.request ?? base.request,
    requestChars: int(d.request_chars) ?? base.requestChars,
    response: d.response ?? null,
    error,
    stopReason: str(d.stop_reason),
    latencyMs: latency,
    attempts: int(d.attempts),
    httpStatuses: statuses(d.http_statuses),
    retryWaitMs: int(d.retry_wait_ms),
    ttfbMs: int(d.ttfb_ms),
    downloadMs: int(d.download_ms),
    overheadMs: int(d.overhead_ms),
    inputTokens: int(d.input_tokens),
    outputTokens: int(d.output_tokens),
    reasoningTokens: int(d.reasoning_tokens),
    upstreamProvider: str(d.upstream_provider),
  };
}

function blank(uid: string, d: Record<string, unknown>): DebugCall {
  return {
    key: uid,
    uid,
    dbId: null,
    state: "running",
    startedMs: time(d.ts) ?? Date.now(),
    finishedMs: null,
    task: str(d.task) ?? "?",
    provider: str(d.provider) ?? "",
    model: str(d.model) ?? "",
    promptVersion: str(d.prompt_version),
    request: d.request ?? null,
    requestChars: int(d.request_chars),
    response: null,
    error: null,
    stopReason: null,
    latencyMs: null,
    attempts: null,
    httpStatuses: null,
    retryWaitMs: null,
    ttfbMs: null,
    downloadMs: null,
    overheadMs: null,
    inputTokens: null,
    outputTokens: null,
    reasoningTokens: null,
    upstreamProvider: null,
  };
}

function fromRow(row: DebugCallRow): DebugCall {
  const finishedMs = Date.parse(row.ts);
  const d = row as unknown as Record<string, unknown>;
  const call = finish(blank(`db:${row.id}`, d), { ...d, db_id: row.id });
  return {
    ...call,
    key: `db:${row.id}`,
    uid: null,
    startedMs: finishedMs - row.latency_ms,
  };
}

function normalize(calls: DebugCall[]): DebugCall[] {
  return [...calls]
    .sort((a, b) => b.startedMs - a.startedMs)
    .slice(0, MAX_CALLS);
}

export function applyMessage(
  calls: DebugCall[],
  ignored: string[],
  { name, data }: LiveMessage,
): DebugCall[] {
  const uid = str(data.id);
  if (!uid || ignored.includes(uid)) return calls;
  const existing = calls.find((c) => c.uid === uid);
  if (name === "call_started") {
    // A reconnect resends started events of calls that are already finished.
    return existing ? calls : normalize([...calls, blank(uid, data)]);
  }
  const dbId = int(data.db_id);
  const rest = calls.filter(
    (c) =>
      c.uid !== uid && !(dbId !== null && c.uid === null && c.dbId === dbId),
  );
  return normalize([...rest, finish(existing ?? blank(uid, data), data)]);
}

export function reducer(state: DebugState, action: Action): DebugState {
  switch (action.type) {
    case "event":
      if (state.paused)
        return {
          ...state,
          pending: [...state.pending, action.message].slice(-MAX_PENDING),
        };
      return {
        ...state,
        calls: applyMessage(state.calls, state.ignored, action.message),
      };
    case "history": {
      const known = new Set(state.calls.map((c) => c.dbId));
      const added = action.rows.filter((r) => !known.has(r.id)).map(fromRow);
      return { ...state, calls: normalize([...state.calls, ...added]) };
    }
    case "clear":
      return {
        ...state,
        calls: [],
        pending: [],
        ignored: [
          ...state.ignored,
          ...state.calls.flatMap((c) => (c.uid ? [c.uid] : [])),
          ...state.pending.flatMap((m) => str(m.data.id) ?? []),
        ],
      };
    case "pause": {
      if (action.paused) return { ...state, paused: true };
      const calls = state.pending.reduce(
        (acc, m) => applyMessage(acc, state.ignored, m),
        state.calls,
      );
      return { ...state, paused: false, pending: [], calls };
    }
  }
}

// --- request shapes ---------------------------------------------------------------------

function textOf(content: unknown): string | null {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const parts = content.map(textOf).filter((t): t is string => t !== null);
    return parts.length > 0 ? parts.join("\n\n") : null;
  }
  if (content && typeof content === "object") {
    const o = content as Record<string, unknown>;
    if (typeof o.text === "string") return o.text;
    if ("parts" in o) return textOf(o.parts);
    if ("content" in o) return textOf(o.content);
  }
  return null;
}

export interface Prompt {
  system: string | null;
  user: string | null;
}

/**
 * System prompt and user content of a logged request. Handles OpenAI-compatible
 * (`messages` with roles), Anthropic (`system` blocks + `messages`) and Gemini
 * (`config.system_instruction` + `contents`). Null where the shape has none (fake client).
 */
export function extractPrompt(request: unknown): Prompt {
  if (!request || typeof request !== "object")
    return { system: null, user: null };
  const r = request as Record<string, unknown>;
  const system: string[] = [];
  const user: string[] = [];
  const top = textOf(r.system);
  if (top) system.push(top);
  const config = r.config as Record<string, unknown> | undefined;
  const instruction = textOf(config?.system_instruction);
  if (instruction) system.push(instruction);
  if (Array.isArray(r.messages)) {
    for (const m of r.messages as Record<string, unknown>[]) {
      const text = textOf(m?.content);
      if (!text) continue;
      (m.role === "system" || m.role === "developer" ? system : user).push(
        text,
      );
    }
  }
  const contents = textOf(r.contents);
  if (contents) user.push(contents);
  return {
    system: system.length > 0 ? system.join("\n\n") : null,
    user: user.length > 0 ? user.join("\n\n") : null,
  };
}

// --- formatting and statistics -----------------------------------------------------------

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** Total time of a call: the elapsed time while it is running. */
export function totalLabel(c: DebugCall, now: number): string {
  if (c.state === "running") return formatMs(Math.max(0, now - c.startedMs));
  return formatMs(c.latencyMs);
}

export function tokensPerSecond(c: DebugCall): number | null {
  if (!c.outputTokens || !c.ttfbMs) return null;
  return c.outputTokens / (c.ttfbMs / 1000);
}

/** "2 tries 429,200" */
export function formatTries(c: DebugCall): string | null {
  if (c.attempts === null) return null;
  const word = c.attempts === 1 ? "try" : "tries";
  const codes = (c.httpStatuses ?? []).map((s) => s ?? "–").join(",");
  return `${c.attempts} ${word}${codes ? ` ${codes}` : ""}`;
}

export function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return NaN;
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  const a = sorted[lo] ?? NaN;
  const b = sorted[hi] ?? NaN;
  return a + (b - a) * (idx - lo);
}

export interface TaskStat {
  task: string;
  n: number;
  p50: number;
  p90: number;
}

/** Per-task p50 / p90 of the total latency of finished calls. */
export function taskStats(calls: DebugCall[]): TaskStat[] {
  const byTask = new Map<string, number[]>();
  for (const c of calls) {
    if (c.state === "running" || c.latencyMs === null) continue;
    byTask.set(c.task, [...(byTask.get(c.task) ?? []), c.latencyMs]);
  }
  return [...byTask.entries()]
    .map(([task, xs]) => {
      const sorted = xs.sort((a, b) => a - b);
      return {
        task,
        n: sorted.length,
        p50: percentile(sorted, 0.5),
        p90: percentile(sorted, 0.9),
      };
    })
    .sort((a, b) => a.task.localeCompare(b.task));
}

export interface TimingSegment {
  key: "wait" | "ttfb" | "download" | "overhead";
  label: string;
  ms: number;
}

/** Segments of the timing bar; null when the call has no breakdown. */
export function timingSegments(c: DebugCall): TimingSegment[] | null {
  const parts: [TimingSegment["key"], string, number | null][] = [
    ["wait", "wait", c.retryWaitMs],
    ["ttfb", "ttfb", c.ttfbMs],
    ["download", "download", c.downloadMs],
    ["overhead", "overhead", c.overheadMs],
  ];
  if (parts.every(([, , ms]) => ms === null)) return null;
  return parts.map(([key, label, ms]) => ({ key, label, ms: ms ?? 0 }));
}
