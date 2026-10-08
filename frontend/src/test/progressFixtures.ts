/** Canned responses for the /api/progress/* endpoints. */

export const SUMMARY = {
  timezone: "Europe/Rome",
  today: "2026-10-08",
  streak: {
    current: 4,
    longest: 9,
    last_study_day: "2026-10-08",
    studied_today: true,
  },
  study_days_total: 31,
  totals: {
    reviews: 410,
    exercises: 52,
    readings: 6,
    items_introduced: 88,
    minutes: 640,
  },
  this_week: {
    study_days: 5,
    reviews: 120,
    exercises: 9,
    readings: 2,
    new_items: 12,
    minutes: 95,
  },
  last_week: {
    study_days: 3,
    reviews: 80,
    exercises: 6,
    readings: 1,
    new_items: 10,
    minutes: 60,
  },
  retention: { observed: 0.87, target: 0.9, n_reviews: 210 },
  states: { new: 100, learning: 10, young: 20, mature: 30, presumed_known: 5 },
  states_words: {
    new: 80,
    learning: 8,
    young: 15,
    mature: 25,
    presumed_known: 4,
  },
  states_grammar: {
    new: 20,
    learning: 2,
    young: 5,
    mature: 5,
    presumed_known: 1,
  },
  due_now: 14,
  due_today: 22,
};

function day(date: string, reviews: number, exercises = 0) {
  return {
    date,
    reviews,
    exercises,
    new_items: 0,
    readings: 0,
    minutes: reviews / 4,
    active: reviews + exercises > 0,
  };
}

export const ACTIVITY = {
  timezone: "Europe/Rome",
  today: "2026-10-08",
  days: [
    day("2026-10-02", 0),
    day("2026-10-03", 12),
    day("2026-10-04", 30, 2),
    day("2026-10-05", 0),
    day("2026-10-06", 55, 3),
    day("2026-10-07", 8),
    day("2026-10-08", 20, 1),
  ],
  weeks: [
    {
      week_start: "2026-09-28",
      flashcards_correct_rate: 0.8,
      production_correct_rate: 0.5,
      flashcards_n: 40,
      production_n: 4,
      n: 44,
    },
    {
      week_start: "2026-10-05",
      flashcards_correct_rate: 0.9,
      production_correct_rate: null,
      flashcards_n: 60,
      production_n: 0,
      n: 60,
    },
  ],
};

export const LEVELS = [
  {
    level: "A1",
    kind: "lemma",
    total: 60,
    introduced: 30,
    new: 20,
    learning: 5,
    young: 10,
    mature: 20,
    presumed_known: 5,
    mean_mastery: 0.7,
  },
  {
    level: "A1",
    kind: "grammar",
    total: 6,
    introduced: 4,
    new: 2,
    learning: 1,
    young: 1,
    mature: 2,
    presumed_known: 0,
    mean_mastery: 0.6,
  },
];

export const FORECAST = Array.from({ length: 14 }, (_, i) => ({
  date: `2026-10-${String(8 + i).padStart(2, "0")}`,
  due: i === 0 ? 14 : i % 3,
}));

export const GRAMMAR_ROW = {
  id: "gram:dativ",
  kind: "grammar",
  level: "A2",
  label: "Dativ",
  translation_it: null,
  status: "introduced",
  state: "learning",
  mastery: 0.42,
  n_eff: 6,
  due: "2026-10-09T08:00:00Z",
  last_practiced: "2026-10-07T10:00:00Z",
  errors: 4,
  answers: 9,
  error_rate: 0.44,
  top_tags: [{ tag: "dativ_feminine", count: 4 }],
  facets: [],
};

export const WORD_ROW = {
  id: "lex:bank#money",
  kind: "lemma",
  level: "A2",
  label: "die Bank",
  translation_it: "banca",
  status: "introduced",
  state: "young",
  mastery: 0.6,
  n_eff: 3,
  due: null,
  last_practiced: "2026-10-06T10:00:00Z",
  errors: 0,
  answers: 5,
  error_rate: 0,
  top_tags: [],
  facets: [
    {
      facet: "recognition",
      mastery: 0.8,
      stability: 5,
      due: null,
      n_eff: 2,
      state: "young",
    },
    {
      facet: "production",
      mastery: 0.4,
      stability: 1,
      due: null,
      n_eff: 1,
      state: "learning",
    },
  ],
};

export function itemList(rows: object[]) {
  // JSON round trip: plain data that fits the mock's JSON type.
  return JSON.parse(
    JSON.stringify({ total: rows.length, limit: 30, offset: 0, items: rows }),
  );
}

const ERROR = {
  start: 4,
  end: 12,
  original: "dem Frau",
  correction: "der Frau",
  item_id: "gram:dativ",
  label: "Dativ",
  diagnostic_tags: ["dativ_feminine"],
  severity: "major",
  confidence: 0.9,
  explanation: "Feminine dative takes der.",
};

export const ANSWER = {
  exercise_id: "ex1",
  attempt_id: 11,
  evaluation_id: 21,
  type: "production",
  subtype: "translation",
  answered_at: "2026-10-07T10:00:00Z",
  outcome: "error",
  prompt: "Dai il libro alla donna.",
  instructions: null,
  options: null,
  answer: "Ich dem Frau",
  expected: "Ich gebe der Frau das Buch.",
  feedback: "Attenzione al dativo.",
  used_hint: false,
  duration_ms: 20000,
  errors: [ERROR],
  items: [
    {
      item_id: "gram:dativ",
      label: "Dativ",
      outcome: "error",
      diagnostic_tags: [],
    },
  ],
  contest: null,
  item_outcome: "error",
  item_errors: [ERROR],
};

export const CARD = {
  exercise_id: "ex2",
  attempt_id: 12,
  evaluation_id: 22,
  type: "flashcard_recognition",
  subtype: null,
  answered_at: "2026-10-07T09:00:00Z",
  outcome: "correct",
  prompt: "banca",
  instructions: null,
  options: ["die Bank", "der Baum"],
  answer: "die Bank",
  expected: "die Bank",
  feedback: null,
  used_hint: false,
  duration_ms: 3000,
  errors: [],
  items: [],
  contest: null,
  item_outcome: null,
  item_errors: [],
};

export function trajectory(facet: string, mastery: number[]) {
  return {
    facet,
    state: "learning",
    mastery: mastery[mastery.length - 1] ?? null,
    stability: 0.8,
    due: "2026-10-09T08:00:00Z",
    n_eff: mastery.length,
    counts: { correct: 5, assisted: 0, error: 4 },
    tag_errors: [{ tag: "dativ_feminine", count: 4 }],
    trajectory_total: mastery.length,
    trajectory: mastery.map((m, i) => ({
      event_id: i + 1,
      ts: `2026-10-0${i + 1}T10:00:00Z`,
      kind: "review",
      outcome: i === 1 ? "error" : "correct",
      mastery: m,
      stability: 1 + i,
    })),
  };
}

export function progressItem(
  over: Record<string, unknown> = {},
  itemOver: Record<string, unknown> = {},
) {
  return {
    item: {
      id: "gram:dativ",
      kind: "grammar",
      level: "A2",
      label: "Dativ",
      translation_it: null,
      payload: { title_it: "Il dativo" },
      interference: {},
      requires: [],
      frequency_zipf: null,
      suspended: false,
      status: "introduced",
      candidate_source: null,
      introduced_at: "2026-10-01T10:00:00Z",
      memory: [],
      ...itemOver,
    },
    state: "learning",
    mastery: 0.42,
    counts: { correct: 5, assisted: 0, error: 4 },
    tag_errors: [{ tag: "dativ_feminine", count: 4 }],
    facets: [trajectory("recognition", [0.3, 0.2, 0.4, 0.5])],
    recent_answers: [ANSWER],
    explanation: null,
    practice_queued: false,
    ...over,
  };
}
