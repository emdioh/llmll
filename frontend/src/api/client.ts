import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Learner = Schemas["LearnerOut"];
export type Settings = Schemas["SettingsOut"];
export type SessionCard = Schemas["SessionCard"];
export type AnswerOut = Schemas["AnswerOut"];
export type ItemSummary = Schemas["ItemSummary"];
export type ItemDetail = Schemas["ItemDetail"];
export type MemoryEntry = Schemas["MemoryEntry"];
export type GrammarSummary = Schemas["GrammarSummary"];
export type GrammarDetail = Schemas["GrammarDetail"];
export type Level = Learner["level"];
export type ItemKind = ItemSummary["kind"];
export type ItemStatus = NonNullable<ItemSummary["status"]>;

/** Error raised for any failed API call. `status` is 0 for network failures. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d: unknown) => (d as { msg?: unknown } | null)?.msg)
      .filter((m): m is string => typeof m === "string");
    if (msgs.length > 0) return msgs.join("; ");
  }
  return `HTTP ${status}`;
}

async function request<T>(
  method: "GET" | "POST" | "PUT",
  path: string,
  body?: unknown,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers:
        body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : String(err));
  }
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailMessage(data, res.status));
  return data as T;
}

export const getHealth = () =>
  request<Schemas["HealthResponse"]>("GET", "/api/health");

export const getLearner = () => request<Learner>("GET", "/api/learner");
export const createLearner = (body: Schemas["LearnerCreate"]) =>
  request<Learner>("POST", "/api/learner", body);
export const updateLearner = (body: Schemas["LearnerUpdate"]) =>
  request<Learner>("PUT", "/api/learner", body);

export const getSettings = () => request<Settings>("GET", "/api/settings");
export const updateSettings = (body: Schemas["SettingsUpdate"]) =>
  request<Settings>("PUT", "/api/settings", body);

export const createSession = () =>
  request<Schemas["SessionOut"]>("POST", "/api/sessions");
export const submitAnswer = (sessionId: string, body: Schemas["AnswerIn"]) =>
  request<AnswerOut>(
    "POST",
    `/api/sessions/${encodeURIComponent(sessionId)}/answers`,
    body,
  );

export interface ItemQuery {
  kind?: ItemKind | "";
  level?: string;
  status?: ItemStatus | "";
  q?: string;
  limit?: number;
  offset?: number;
}

export function listItems(query: ItemQuery) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return request<Schemas["ItemList"]>("GET", `/api/items${qs ? `?${qs}` : ""}`);
}
export const getItem = (id: string) =>
  request<ItemDetail>("GET", `/api/items/${encodeURIComponent(id)}`);

export const listGrammar = () =>
  request<GrammarSummary[]>("GET", "/api/grammar");
export const getGrammar = (id: string) =>
  request<GrammarDetail>("GET", `/api/grammar/${encodeURIComponent(id)}`);
