import { MemoryRouter } from "react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LearnerContext } from "../learnerContext";
import { LEARNER, mockApi } from "../test/mockApi";
import type { Learner } from "../api/client";
import SessionView from "./SessionView";

function renderView() {
  return render(
    <LearnerContext.Provider
      value={{ learner: LEARNER as Learner, setLearner: () => {} }}
    >
      <MemoryRouter>
        <SessionView />
      </MemoryRouter>
    </LearnerContext.Provider>,
  );
}

const pendingCard = {
  exercise_id: "p1",
  type: "production",
  item_id: "gram:akk",
  status: "pending",
  subtype: "translation",
  prompt: { text: null },
  glossary: [],
  item_ids: ["gram:akk"],
};

const readyCard = {
  ...pendingCard,
  status: "ready",
  instructions: "Traduci in tedesco.",
  prompt: { text: "Vedo il cane." },
  glossary: [
    { item_id: "lex:hund", de: "der Hund", translation: "cane" },
    { item_id: "lex:sehen", de: "sehen", translation: "vedere" },
  ],
  item_ids: ["gram:akk", "lex:hund"],
};

const grade = {
  kind: "production",
  outcome: "major_errors",
  corrected_sentence: "Ich sehe den Hund.",
  errors: [
    {
      start: 9,
      end: 12,
      original: "der",
      correction: "den",
      item_id: "gram:akk",
      diagnostic_tags: ["case"],
      severity: "major",
      confidence: 0.9,
      explanation: "Accusativo maschile: den.",
    },
  ],
  feedback: "Quasi: attenzione al **caso**.",
  items: [
    {
      item_id: "gram:akk",
      label: "Accusativo",
      outcome: "error",
      needs_remediation: true,
    },
    {
      item_id: "lex:hund",
      label: "Hund",
      outcome: "correct",
      needs_remediation: false,
    },
  ],
  evaluation_id: 7,
};

describe("SessionView production flow", () => {
  it("prepares, grades, highlights errors and auto-explains remediation items", async () => {
    const explained: unknown[] = [];
    let preparedAt = 0;
    mockApi({
      "POST /api/sessions": () => ({
        status: 201,
        body: { session_id: "s1", cards: [pendingCard] },
      }),
      "POST /api/exercises/p1/prepare": () => {
        preparedAt++;
        return { body: { ...readyCard, fallback_cards: [] } };
      },
      "POST /api/sessions/s1/answers": ({ body }) => {
        expect(body).toMatchObject({
          exercise_id: "p1",
          answer: { text: "Ich sehe der Hund." },
        });
        return { body: grade };
      },
      "POST /api/evaluations/7/explain": ({ body }) => {
        explained.push(body);
        return {
          body: {
            markdown: "Dopo *sehen* serve l'**accusativo**.",
            examples: [],
            cached: false,
          },
        };
      },
    });
    renderView();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start session" }));

    expect(await screen.findByText("Vedo il cane.")).toBeInTheDocument();
    expect(preparedAt).toBe(1);
    expect(screen.getByText("Traduci in tedesco.")).toBeInTheDocument();
    const glossary = screen.getByRole("list", { name: "Glossary" });
    const items = within(glossary).getAllByRole("listitem");
    expect(items[0]).toHaveClass("new");
    expect(items[1]).not.toHaveClass("new");

    const field = screen.getByLabelText("Your answer");
    expect(field.tagName).toBe("TEXTAREA");
    expect(field).toHaveAttribute("lang", "de");
    expect(field).toHaveAttribute("autocorrect", "off");
    await user.type(field, "Ich sehe der Hund.");
    await user.click(screen.getByRole("button", { name: "Check" }));

    const err = await screen.findByRole("button", { name: "der" });
    expect(err).toHaveClass("err", "major");
    expect(err).toHaveAttribute("aria-expanded", "false");
    await user.click(err);
    expect(screen.getByText("Accusativo maschile: den.")).toBeInTheDocument();
    expect(screen.getByText("den", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("Ich sehe den Hund.")).toBeInTheDocument();
    expect(screen.getByText("caso").tagName).toBe("STRONG");
    expect(screen.getByText("Needs work")).toBeInTheDocument();

    // remediation item explained automatically, the other on demand
    expect(await screen.findByText("accusativo")).toBeInTheDocument();
    expect(explained).toEqual([{ item_id: "gram:akk" }]);
    expect(
      screen.getAllByRole("button", { name: "Spiegami meglio" }),
    ).toHaveLength(1);
    const later = screen.getByRole("button", {
      name: /Secondo me era giusto/,
    });
    expect(later).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Finish" }));
    expect(
      await screen.findByRole("heading", { name: "Session complete" }),
    ).toBeInTheDocument();
  });

  it("shows a spinner while preparing, then the card", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const fn = mockApi({
      "POST /api/sessions": () => ({
        status: 201,
        body: { session_id: "s1", cards: [pendingCard] },
      }),
      "POST /api/exercises/p1/prepare": () => ({
        body: { ...readyCard, fallback_cards: [] },
      }),
    });
    const real = globalThis.fetch;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes("/prepare")
        ? gate.then(() => real(input, init))
        : real(input, init)) as typeof fetch;
    renderView();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    expect(await screen.findByText("Preparing your exercise…")).toBeVisible();
    release();
    expect(await screen.findByText("Vedo il cane.")).toBeInTheDocument();
    expect(fn).toHaveBeenCalled();
  });

  it("replaces a failed exercise with its fallback cards", async () => {
    mockApi({
      "POST /api/sessions": () => ({
        status: 201,
        body: { session_id: "s1", cards: [pendingCard] },
      }),
      "POST /api/exercises/p1/prepare": () => ({
        body: {
          ...pendingCard,
          status: "failed",
          fallback_cards: [
            {
              exercise_id: "f1",
              type: "flashcard_intro",
              item_id: "lex:hund",
              status: "ready",
              prompt: { lemma: "Hund", article: "der", translation_it: "cane" },
            },
          ],
        },
      }),
      "POST /api/sessions/s1/answers": () => ({
        body: {
          kind: "flashcard",
          outcome: "correct",
          expected: null,
          diagnostic_tags: [],
          feedback_it: "",
          memory: null,
        },
      }),
    });
    renderView();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start session" }));
    expect(await screen.findByText("New word")).toBeInTheDocument();
    expect(screen.getByText(/Card 1 of 1/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Got it" }));
    expect(
      await screen.findByRole("heading", { name: "Session complete" }),
    ).toBeInTheDocument();
  });

  it("keeps the text and retries after a 503 from grading", async () => {
    const texts: string[] = [];
    mockApi({
      "POST /api/sessions": () => ({
        status: 201,
        body: { session_id: "s1", cards: [{ ...readyCard }] },
      }),
      "POST /api/sessions/s1/answers": ({ body }) => {
        texts.push((body as { answer: { text: string } }).answer.text);
        return texts.length === 1
          ? { status: 503, body: { detail: "LLM unavailable" } }
          : { body: { ...grade, outcome: "correct", errors: [] } };
      },
      "POST /api/evaluations/7/explain": {
        markdown: "Spiegazione.",
        examples: [],
        cached: false,
      },
    });
    renderView();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start session" }));
    await user.type(await screen.findByLabelText("Your answer"), "Ich sehe");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/try again/i);
    expect(screen.getByLabelText("Your answer")).toHaveValue("Ich sehe");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Correct")).toBeInTheDocument();
    expect(texts).toEqual(["Ich sehe", "Ich sehe"]);
  });

  it("shows a grammar_intro card and acknowledges it", async () => {
    const answers: unknown[] = [];
    mockApi({
      "POST /api/sessions": () => ({
        status: 201,
        body: {
          session_id: "s1",
          cards: [
            {
              exercise_id: "g1",
              type: "grammar_intro",
              item_id: "gram:akk",
              status: "ready",
              prompt: {
                title: "Accusativo",
                reference_it: "## Regola\n\nUsa **den** per il maschile.",
                examples: [{ de: "Ich sehe den Hund.", it: "Vedo il cane." }],
              },
            },
          ],
        },
      }),
      "POST /api/sessions/s1/answers": ({ body }) => {
        answers.push(body);
        return {
          body: {
            kind: "flashcard",
            outcome: "correct",
            expected: null,
            diagnostic_tags: [],
            feedback_it: "",
            memory: null,
          },
        };
      },
    });
    renderView();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start session" }));
    expect(
      await screen.findByRole("heading", { name: "Accusativo" }),
    ).toBeInTheDocument();
    expect(screen.getByText("den").tagName).toBe("STRONG");
    expect(screen.getByText("Vedo il cane.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Got it" }));
    expect(
      await screen.findByRole("heading", { name: "Session complete" }),
    ).toBeInTheDocument();
    expect(answers[0]).toMatchObject({ exercise_id: "g1", answer: {} });
  });

  it("highlights the correct recognition option", async () => {
    mockApi({
      "POST /api/sessions": () => ({
        status: 201,
        body: {
          session_id: "s1",
          cards: [
            {
              exercise_id: "r1",
              type: "flashcard_recognition",
              item_id: "lex:haus",
              status: "ready",
              prompt: {
                de: "das Haus",
                options: ["gatto", "cane", "casa", "pane"],
              },
            },
          ],
        },
      }),
      "POST /api/sessions/s1/answers": () => ({
        body: {
          kind: "flashcard",
          outcome: "error",
          expected: {
            text: "das Haus",
            lemma: "Haus",
            translation_it: "casa",
            correct_index: 2,
          },
          diagnostic_tags: [],
          feedback_it: "",
          memory: null,
        },
      }),
    });
    renderView();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start session" }));
    await user.click(await screen.findByRole("button", { name: "gatto" }));
    await screen.findByText("Incorrect");
    expect(screen.getByRole("button", { name: "casa" })).toHaveClass("reveal");
    expect(screen.getByRole("button", { name: "gatto" })).toHaveClass(
      "picked",
      "error",
    );
    expect(screen.getByRole("button", { name: "cane" })).not.toHaveClass(
      "reveal",
    );
  });
});
