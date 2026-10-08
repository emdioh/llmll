import { vi } from "vitest";

export type Handler = (req: {
  method: string;
  url: URL;
  body: unknown;
}) => { status?: number; body?: unknown } | undefined;

/**
 * Stubs global fetch with a handler keyed by "METHOD /path". Unhandled
 * requests fail loudly. Returns the mock so tests can inspect calls.
 */
type Json = string | number | boolean | null | Json[] | { [k: string]: Json };

export function mockApi(routes: Record<string, Handler | Json>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    const key = `${method} ${url.pathname}`;
    const route = routes[key];
    if (route === undefined) throw new Error(`Unhandled request: ${key}`);
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const res =
      typeof route === "function"
        ? ((route as Handler)({ method, url, body }) ?? {})
        : { body: route };
    return new Response(JSON.stringify(res.body ?? null), {
      status: res.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const LEARNER = {
  id: 1,
  level: "A2",
  known_languages: ["it", "en"],
  explanation_language: "it",
  settings: {
    weekly_new_lemmas: 20,
    weekly_new_grammar: 2,
    desired_retention: 0.85,
    review_cap: 15,
    new_per_session: 5,
    production_slots: 2,
    timezone: "UTC",
  },
};

export const AUTH_OFF = { auth: "disabled", authenticated: true };
export const STATS = {
  reviews_7d: 42,
  reviews_30d: 120,
  sessions_7d: 5,
  readings_7d: 2,
  new_items_7d: 17,
  observed_retention: 0.87,
  target_retention: 0.9,
  backlog: 9,
  calibration: [
    {
      bucket_low: 0.8,
      bucket_high: 0.9,
      n: 30,
      predicted: 0.85,
      observed: 0.8,
      recalled: 24,
      assisted: 1,
      forgotten: 5,
    },
  ],
};
