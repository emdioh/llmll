import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Learner = Schemas["LearnerOut"];
export type Settings = Schemas["SettingsOut"];
export type SessionCard = Schemas["SessionCard"];
export type FlashcardAnswer = Schemas["AnswerOut"];
export type ProductionAnswer = Schemas["ProductionAnswerOut"];
/** Answer response, discriminated by `kind`. */
export type AnswerOut = FlashcardAnswer | ProductionAnswer;
export type PreparedCard = Schemas["PreparedCard"];
export type Explanation = Schemas["ExplanationOut"];
export type AnswerError = Schemas["AnswerError"];
export type ItemResult = Schemas["ItemResult"];
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

/** Fired when the API answers 401 (not for the login call itself). */
export const UNAUTHORIZED_EVENT = "llmll:unauthorized";

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
  if (res.status === 401 && path !== "/api/auth/login")
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  if (!res.ok) throw new ApiError(res.status, detailMessage(data, res.status));
  return data as T;
}

export const getHealth = () =>
  request<Schemas["HealthResponse"]>("GET", "/api/health");

export type DebugCallRow = Schemas["DebugCallOut"];
export const getDebugCalls = (limit = 50) =>
  request<DebugCallRow[]>("GET", `/api/debug/llm/calls?limit=${limit}`);

export type AuthStatus = Schemas["AuthStatus"];
export type Stats = Schemas["StatsOut"];
export const getAuthStatus = () =>
  request<AuthStatus>("GET", "/api/auth/status");
export const login = (token: string) =>
  request<AuthStatus>("POST", "/api/auth/login", {
    token,
  } satisfies Schemas["LoginIn"]);
export const logout = () => request<AuthStatus>("POST", "/api/auth/logout");
export const getStats = () => request<Stats>("GET", "/api/stats");

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
export const prepareExercise = (exerciseId: string) =>
  request<PreparedCard>(
    "POST",
    `/api/exercises/${encodeURIComponent(exerciseId)}/prepare`,
  );
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

export const explainEvaluation = (evaluationId: number, itemId: string) =>
  request<Explanation>("POST", `/api/evaluations/${evaluationId}/explain`, {
    item_id: itemId,
  } satisfies Schemas["EvaluationExplainIn"]);
export const explainGrammar = (id: string, question: string) =>
  request<Explanation>(
    "POST",
    `/api/grammar/${encodeURIComponent(id)}/explain`,
    { question } satisfies Schemas["GrammarExplainIn"],
  );

export type TextOut = Schemas["TextOut"];
export type TextSummary = Schemas["TextSummary"];
export type TextVersion = Schemas["VersionOut"];
export type TextToken = Schemas["TokenOut"];
export type WordClass = TextToken["word_class"];
export type Gloss = Schemas["GlossOut"];
export type OptinResult = Schemas["OptinOut"];
export type ReadingStart = Schemas["ReadingOut"];
export type FinishResult = Schemas["FinishOut"];

export const listTexts = () => request<TextSummary[]>("GET", "/api/texts");
export const getText = (id: number) =>
  request<TextOut>("GET", `/api/texts/${id}`);
export const createText = (body: Schemas["CreateTextIn"]) =>
  request<TextOut>("POST", "/api/texts", body);
export const generateText = (topic?: string) =>
  request<TextOut>("POST", "/api/texts/generate", {
    topic: topic || null,
  } satisfies Schemas["GenerateTextIn"]);
export const startReading = (textId: number) =>
  request<ReadingStart>("POST", `/api/texts/${textId}/reading`);
export const glossToken = (readingId: number, tokenIndex: number) =>
  request<Gloss>("POST", `/api/reading/${readingId}/gloss`, {
    token_index: tokenIndex,
  } satisfies Schemas["GlossIn"]);
export const optinToken = (readingId: number, tokenIndex: number) =>
  request<OptinResult>("POST", `/api/reading/${readingId}/optin`, {
    token_index: tokenIndex,
  } satisfies Schemas["GlossIn"]);
export const finishReading = (readingId: number) =>
  request<FinishResult>("POST", `/api/reading/${readingId}/finish`);

export type Queue = Schemas["QueueOut"];
export type QueueEntry = Schemas["QueueEntry"];
export type OptStatus = Schemas["OptStatusOut"];
export type ContestResult = Schemas["ContestResultOut"];
export type ContestRecord = Schemas["ContestOut"];
export type ContestItemOutcome = Schemas["ContestItemOutcome"];
export type Placement = Schemas["PlacementOut"];
export type PlacementFinish = Schemas["PlacementFinishOut"];

export const getQueue = () => request<Queue>("GET", "/api/queue");
export const optinItem = (itemId: string) =>
  request<OptStatus>("POST", `/api/items/${encodeURIComponent(itemId)}/optin`);
export const optoutItem = (itemId: string) =>
  request<OptStatus>("POST", `/api/items/${encodeURIComponent(itemId)}/optout`);

/** Contests an evaluation: the whole answer, or only `itemIds` when given. */
export const contestEvaluation = (
  evaluationId: number,
  itemIds: string[] = [],
  reason?: string,
) =>
  request<ContestResult>("POST", `/api/evaluations/${evaluationId}/contest`, {
    item_ids: itemIds,
    reason: reason?.trim() ? reason.trim() : null,
  } satisfies Schemas["ContestIn"]);
/** Only needed for flashcard attempts recorded before M4 (no evaluation id). */
export const contestAttempt = (
  attemptId: number,
  itemIds: string[] = [],
  reason?: string,
) =>
  request<ContestResult>("POST", `/api/attempts/${attemptId}/contest`, {
    item_ids: itemIds,
    reason: reason?.trim() ? reason.trim() : null,
  } satisfies Schemas["ContestIn"]);
export const listContests = (limit = 50) =>
  request<ContestRecord[]>("GET", `/api/contests?limit=${limit}`);

export const startPlacement = () =>
  request<Placement>("POST", "/api/placement");
export const finishPlacement = (placementId: string) =>
  request<PlacementFinish>(
    "POST",
    `/api/placement/${encodeURIComponent(placementId)}/finish`,
  );

// ---- Progress (M8) ----
export type ProgressSummary = Schemas["SummaryOut"];
export type ProgressActivity = Schemas["ActivityOut"];
export type ActivityDay = Schemas["DayOut"];
export type WeekAccuracy = Schemas["WeekAccuracyOut"];
export type LevelProgress = Schemas["LevelProgressOut"];
export type ForecastDay = Schemas["ForecastDayOut"];
export type ProgressItemRow = Schemas["ProgressItemRow"];
export type ProgressItemList = Schemas["ProgressItemList"];
export type ProgressItemDetail = Schemas["ProgressItemDetail"];
export type FacetDetail = Schemas["FacetDetailOut"];
export type TrajectoryPoint = Schemas["TrajectoryPointOut"];
export type AnswerCard = Schemas["AnswerCardOut"];
export type HistoryList = Schemas["HistoryList"];
export type HistoryRow = Schemas["HistoryRow"];
export type SessionDetail = Schemas["SessionDetailOut"];
export type ReadingDetail = Schemas["ReadingDetailOut"];
export type PracticeResult = Schemas["PracticeOut"];
export type MemoryState = ProgressItemRow["state"];
export type ProgressSort =
  "weakest" | "strongest" | "recent" | "due" | "errors";

export interface ProgressItemQuery {
  kind?: ItemKind;
  sort?: ProgressSort;
  q?: string;
  level?: string;
  state?: MemoryState | "";
  limit?: number;
  offset?: number;
}

export const getProgressSummary = () =>
  request<ProgressSummary>("GET", "/api/progress/summary");
export const getProgressActivity = (days = 140) =>
  request<ProgressActivity>("GET", `/api/progress/activity?days=${days}`);
export const getProgressLevels = () =>
  request<LevelProgress[]>("GET", "/api/progress/levels");
export const getProgressForecast = (days = 14) =>
  request<ForecastDay[]>("GET", `/api/progress/forecast?days=${days}`);
export function listProgressItems(query: ProgressItemQuery) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return request<ProgressItemList>(
    "GET",
    `/api/progress/items?${params.toString()}`,
  );
}
export const getProgressItem = (id: string) =>
  request<ProgressItemDetail>(
    "GET",
    `/api/progress/items/${encodeURIComponent(id)}`,
  );
export const practiceItem = (id: string) =>
  request<PracticeResult>(
    "POST",
    `/api/progress/items/${encodeURIComponent(id)}/practice`,
  );
export const getProgressHistory = (limit = 20, offset = 0) =>
  request<HistoryList>(
    "GET",
    `/api/progress/history?limit=${limit}&offset=${offset}`,
  );
export const getProgressSession = (id: string) =>
  request<SessionDetail>(
    "GET",
    `/api/progress/history/session/${encodeURIComponent(id)}`,
  );
export const getProgressReading = (id: string | number) =>
  request<ReadingDetail>(
    "GET",
    `/api/progress/history/reading/${encodeURIComponent(String(id))}`,
  );
