import { MemoryRouter, Route, Routes } from "react-router";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Learner } from "../api/client";
import { LearnerContext } from "../learnerContext";
import { LEARNER, mockApi } from "../test/mockApi";
import PlacementView from "./PlacementView";

function renderView(setLearner = vi.fn()) {
  render(
    <LearnerContext.Provider
      value={{ learner: LEARNER as Learner, setLearner }}
    >
      <MemoryRouter initialEntries={["/placement"]}>
        <Routes>
          <Route path="/placement" element={<PlacementView />} />
          <Route path="/" element={<h1>Home</h1>} />
        </Routes>
      </MemoryRouter>
    </LearnerContext.Provider>,
  );
  return setLearner;
}

const vocab = (n: number) => ({
  exercise_id: `v${n}`,
  type: "flashcard_recognition",
  item_id: `lex:w${n}`,
  status: "ready",
  prompt: { de: `Wort${n}`, options: ["uno", "due", "tre", "quattro"] },
});

const grammarPending = {
  exercise_id: "g1",
  type: "production",
  item_id: "gram:inv",
  status: "pending",
  subtype: "guided",
  prompt: { text: null },
  glossary: [],
  item_ids: ["gram:inv"],
};

const grammarReady = {
  ...grammarPending,
  status: "ready",
  instructions: "Scrivi una frase.",
  prompt: { text: "Heute ... ich (gehen)." },
};

describe("PlacementView", () => {
  it("runs vocabulary and grammar cards and shows the level change", async () => {
    const answers: { exercise_id: string; answer: unknown }[] = [];
    const learnerAfter = { ...LEARNER, level: "B1" };
    mockApi({
      "POST /api/placement": () => ({
        status: 201,
        body: {
          placement_id: "placement-1",
          cards: [vocab(1), vocab(2), grammarPending],
        },
      }),
      "POST /api/exercises/g1/prepare": {
        ...grammarReady,
        fallback_cards: [],
      },
      "POST /api/sessions/placement-1/answers": ({ body }) => {
        const b = body as { exercise_id: string; answer: unknown };
        answers.push(b);
        if (b.exercise_id === "g1")
          return {
            body: {
              kind: "production",
              outcome: "correct",
              corrected_sentence: "",
              errors: [],
              feedback: "",
              items: [],
              evaluation_id: 31,
            },
          };
        return {
          body: {
            kind: "flashcard",
            outcome: "correct",
            expected: null,
            diagnostic_tags: [],
            feedback_it: "",
            memory: null,
            evaluation_id: 30,
          },
        };
      },
      "POST /api/placement/placement-1/finish": {
        placement_id: "placement-1",
        estimated_level: "B1",
        previous_level: "A2",
        changed: true,
        answered: 3,
        bands: {},
      },
      "GET /api/learner": learnerAfter,
    });
    const setLearner = renderView();
    const user = userEvent.setup();
    expect(screen.getByText(/Quick placement test/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start the test" }));

    // vocabulary: "Lo so" leads to the 4-option check
    expect(await screen.findByText("Wort1")).toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Lo so" }));
    expect(screen.getByText("What does it mean?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "due" }));

    // "Non lo so" is sent without a choice
    expect(await screen.findByText("Wort2")).toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Non lo so" }));

    // grammar exercise (prepared like a production card)
    expect(
      await screen.findByText("Heute ... ich (gehen)."),
    ).toBeInTheDocument();
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Your answer"), "Heute gehe ich.");
    await user.click(screen.getByRole("button", { name: "Check" }));
    await user.click(await screen.findByRole("button", { name: "Finish" }));

    expect(
      await screen.findByRole("heading", { name: "Placement result" }),
    ).toBeInTheDocument();
    expect(screen.getByText("B1", { selector: "strong" })).toBeInTheDocument();
    expect(
      screen.getByText("Your level was updated from A2 to B1."),
    ).toBeInTheDocument();
    expect(answers).toEqual([
      { exercise_id: "v1", answer: { choice: 1 }, used_hint: false },
      { exercise_id: "v2", answer: {}, used_hint: false },
      {
        exercise_id: "g1",
        answer: { text: "Heute gehe ich." },
        used_hint: false,
      },
    ]);
    expect(setLearner).toHaveBeenCalledWith(learnerAfter);
  });

  it("reports an unchanged level", async () => {
    mockApi({
      "POST /api/placement": () => ({
        status: 201,
        body: { placement_id: "placement-2", cards: [vocab(1)] },
      }),
      "POST /api/sessions/placement-2/answers": {
        kind: "flashcard",
        outcome: "error",
        expected: null,
        diagnostic_tags: [],
        feedback_it: "",
        memory: null,
      },
      "POST /api/placement/placement-2/finish": {
        placement_id: "placement-2",
        estimated_level: "A2",
        previous_level: "A2",
        changed: false,
        answered: 1,
        bands: {},
      },
      "GET /api/learner": LEARNER,
    });
    renderView();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start the test" }));
    await user.click(await screen.findByRole("button", { name: "Non lo so" }));
    expect(await screen.findByText("Your level stays A2.")).toBeInTheDocument();
  });

  it("can be skipped without calling the API", async () => {
    const fn = mockApi({});
    renderView();
    await userEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(
      await screen.findByRole("heading", { name: "Home" }),
    ).toBeInTheDocument();
    expect(fn).not.toHaveBeenCalled();
  });
});
