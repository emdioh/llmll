import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";
import App from "../App";
import { LEARNER, mockApi } from "../test/mockApi";

const BODY = "Der Hund läuft schnell.\n\nBerlin ist groß.";

function tokens() {
  const classes: Record<string, string> = {
    Der: "ignore",
    Hund: "known",
    läuft: "auto_candidate",
    schnell: "optin",
    Berlin: "ignore",
    ist: "known",
    groß: "known",
  };
  const out: Record<string, unknown>[] = [];
  let i = 0;
  for (const m of BODY.matchAll(/[\p{L}]+|[.]/gu)) {
    const alpha = m[0] !== ".";
    out.push({
      i: i++,
      start: m.index,
      end: m.index + m[0].length,
      lemma: m[0],
      is_alpha: alpha,
      word_class: alpha ? classes[m[0]] : "ignore",
      item_id: alpha ? `lex:${m[0].toLowerCase()}` : null,
      parts: [],
    });
  }
  return out;
}

const TEXT = {
  id: 5,
  source_title: "Orig",
  source_url: null,
  source_language: "de",
  original: "Original source text.",
  created_at: "2026-01-02T10:00:00Z",
  version: {
    id: 9,
    level: "A2",
    title: "Der Hund",
    body: BODY,
    coverage: 0.96,
    attempt: 1,
    tokens: tokens(),
    new_words: [],
    notes: "",
  },
};

const exercise = {
  exercise_id: "ex-sum",
  type: "production",
  item_id: "lex:hund",
  status: "ready",
  subtype: "summary",
  instructions: "Riassumi il testo.",
  prompt: { text: "Der Hund" },
  glossary: [],
  item_ids: ["lex:hund"],
};

const grade = {
  kind: "production",
  outcome: "correct",
  corrected_sentence: null,
  errors: [],
  feedback: "Bene!",
  items: [],
  evaluation_id: 3,
};

function base(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    "GET /api/learner": LEARNER,
    "GET /api/texts": [],
    "GET /api/texts/5": TEXT,
    "POST /api/texts/5/reading": () => ({
      status: 201,
      body: { id: 11, text_version_id: 9, started_at: "2026-01-02T10:00:00Z" },
    }),
    ...extra,
  } as Parameters<typeof mockApi>[0]);
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

async function openReader() {
  renderAt("/reading/5");
  return screen.findByRole("button", { name: "Hund" });
}

beforeEach(() => {
  try {
    localStorage.clear();
  } catch {
    // ignore
  }
});

describe("Reading list and new text", () => {
  it("lists texts with title, date and coverage", async () => {
    base({
      "GET /api/texts": [
        {
          id: 5,
          title: "Der Hund",
          source_title: "x",
          source_url: null,
          level: "A2",
          coverage: 0.96,
          created_at: "2026-01-02T10:00:00Z",
          reading_count: 0,
        },
      ],
    });
    renderAt("/reading");
    const link = await screen.findByRole("link", { name: /Der Hund/ });
    expect(link).toHaveAttribute("href", "/reading/5");
    expect(link).toHaveTextContent("96% known words");
  });

  it("creates a text from pasted text and shows the reader with tappable tokens", async () => {
    let posted: unknown = null;
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    base({
      "POST /api/texts": ({ body }) => {
        posted = body;
        return { status: 201, body: TEXT };
      },
    });
    const real = globalThis.fetch;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" && String(input) === "/api/texts"
        ? gate.then(() => real(input, init))
        : real(input, init)) as typeof fetch;
    renderAt("/reading");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "New text" }));
    await user.click(screen.getByRole("tab", { name: "Paste text" }));
    await user.type(screen.getByLabelText("Text"), "Der Hund läuft schnell.");
    await user.click(screen.getByRole("button", { name: "Simplify" }));
    expect(
      await screen.findByText(/Simplifying for your level/),
    ).toBeInTheDocument();
    release();
    expect(await screen.findByRole("button", { name: "Hund" })).toBeVisible();
    expect(posted).toEqual({ text: "Der Hund läuft schnell." });
    // words are buttons, punctuation is not
    expect(screen.getAllByRole("button", { name: "schnell" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "." })).toBeNull();
    expect(screen.getByText("96% known words")).toBeInTheDocument();
  });

  it("on URL extraction failure switches to paste and keeps the URL", async () => {
    base({
      "POST /api/texts": () => ({
        status: 422,
        body: { detail: "Paywalled page." },
      }),
    });
    renderAt("/reading");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "New text" }));
    await user.type(
      screen.getByLabelText("Article URL"),
      "https://example.com/a",
    );
    await user.click(screen.getByRole("button", { name: "Simplify" }));
    expect(await screen.findByText(/Paywalled page\./)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Paste text" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(screen.getByRole("tab", { name: "URL" }));
    expect(screen.getByLabelText("Article URL")).toHaveValue(
      "https://example.com/a",
    );
  });

  it("offers a retry on 503", async () => {
    base({
      "POST /api/texts": () => ({ status: 503, body: { detail: "LLM down" } }),
    });
    renderAt("/reading");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "New text" }));
    await user.type(screen.getByLabelText("Article URL"), "https://e.com/a");
    await user.click(screen.getByRole("button", { name: "Simplify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/try again/i);
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("generates a text with a topic", async () => {
    let posted: unknown = null;
    base({
      "POST /api/texts/generate": ({ body }) => {
        posted = body;
        return { status: 201, body: TEXT };
      },
    });
    renderAt("/reading");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/Generate a text/), "cucina");
    await user.click(screen.getByRole("button", { name: "Generate a text" }));
    expect(await screen.findByRole("button", { name: "Hund" })).toBeVisible();
    expect(posted).toEqual({ topic: "cucina" });
  });
});

describe("Reader", () => {
  it("shows a gloss sheet and opts a word in, then disables the button", async () => {
    let optins = 0;
    base({
      "POST /api/reading/11/gloss": ({ body }) => ({
        body: {
          translation:
            body && (body as { token_index: number }).token_index === 1
              ? "cane"
              : "veloce",
          lemma:
            (body as { token_index: number }).token_index === 1
              ? "Hund"
              : "schnell",
          pos: "NOUN",
          gender: "der",
          plural: "Hunde",
          note: "Nota utile",
          source: "lexicon",
          item_id: "lex:x",
          word_class: "optin",
          can_optin: (body as { token_index: number }).token_index !== 1,
        },
      }),
      "POST /api/reading/11/optin": ({ body }) => {
        optins++;
        expect(body).toEqual({ token_index: 3 });
        return {
          body: {
            item_id: "lex:schnell",
            label: "schnell",
            status: "candidate",
            created: false,
          },
        };
      },
    });
    const user = userEvent.setup();
    await openReader();
    await user.click(screen.getByRole("button", { name: "Hund" }));
    const sheet = await screen.findByRole("dialog", { name: "Word gloss" });
    expect(await within(sheet).findByText("cane")).toBeInTheDocument();
    expect(within(sheet).getByText(/Hunde/)).toBeInTheDocument();
    expect(within(sheet).getByText("Nota utile")).toBeInTheDocument();
    expect(
      within(sheet).queryByRole("button", {
        name: "Aggiungi alle parole da imparare",
      }),
    ).toBeNull();

    await user.click(screen.getByRole("button", { name: "schnell" }));
    const add = await screen.findByRole("button", {
      name: "Aggiungi alle parole da imparare",
    });
    await user.click(add);
    expect(await screen.findByText(/Aggiunta alle parole/)).toBeInTheDocument();
    expect(add).toBeDisabled();
    expect(optins).toBe(1);
  });

  it("toggles underline for new words and remembers it", async () => {
    base();
    const user = userEvent.setup();
    await openReader();
    const schnell = screen.getByRole("button", { name: "schnell" });
    const laeuft = screen.getByRole("button", { name: "läuft" });
    const hund = screen.getByRole("button", { name: "Hund" });
    expect(schnell).not.toHaveClass("new");
    await user.click(screen.getByLabelText("Underline new words"));
    expect(schnell).toHaveClass("new");
    expect(laeuft).toHaveClass("new");
    expect(hund).not.toHaveClass("new");
    expect(localStorage.getItem("llmll.reader.underline")).toBe("1");
  });

  it("shows the original text", async () => {
    base();
    const user = userEvent.setup();
    await openReader();
    await user.click(screen.getByLabelText("Show original"));
    expect(screen.getByText("Original source text.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hund" })).toBeNull();
  });

  it("finishes, shows the result and the answerable summary exercise", async () => {
    let answered: unknown = null;
    base({
      "POST /api/reading/11/finish": () => ({
        body: {
          session_id: "reading-11",
          exercise,
          implicit_events: 12,
          candidates: ["a", "b", "c", "d", "e"],
        },
      }),
      "POST /api/sessions/reading-11/answers": ({ body }) => {
        answered = body;
        return { body: grade };
      },
    });
    const user = userEvent.setup();
    await openReader();
    await user.click(screen.getByRole("button", { name: "Finish" }));
    expect(
      await screen.findByText("12 words reviewed, 5 new words added"),
    ).toBeInTheDocument();
    expect(screen.getByText("Riassumi il testo.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Your answer"), "Der Hund läuft.");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText("Correct")).toBeInTheDocument();
    expect(answered).toMatchObject({
      exercise_id: "ex-sum",
      answer: { text: "Der Hund läuft." },
    });
    expect(screen.getByRole("link", { name: "Back to texts" })).toBeVisible();
  });
});

describe("Session view", () => {
  it("links to reading", async () => {
    mockApi({ "GET /api/learner": LEARNER });
    renderAt("/");
    expect(
      await screen.findByRole("link", { name: "Read something" }),
    ).toHaveAttribute("href", "/reading");
  });
});
