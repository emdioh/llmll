import { MemoryRouter, Route, Routes } from "react-router";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LearnerContext } from "../learnerContext";
import { LEARNER, mockApi } from "../test/mockApi";
import type { Learner } from "../api/client";
import DrillView from "./DrillView";

function renderDrill() {
  return render(
    <LearnerContext.Provider
      value={{ learner: LEARNER as Learner, setLearner: () => {} }}
    >
      <MemoryRouter initialEntries={["/grammar/gram%3Aakk/drill"]}>
        <Routes>
          <Route path="/grammar/:id/drill" element={<DrillView />} />
        </Routes>
      </MemoryRouter>
    </LearnerContext.Provider>,
  );
}

const base = {
  type: "production",
  item_id: "gram:akk",
  item_ids: ["gram:akk"],
  glossary: [],
};
const pending = (id: string, subtype: string) => ({
  ...base,
  exercise_id: id,
  status: "pending",
  subtype,
  prompt: { text: null },
});

const grade = (corrected: string) => ({
  kind: "production",
  outcome: "correct",
  corrected_sentence: corrected,
  errors: [],
  feedback: "Bene.",
  items: [],
  evaluation_id: 3,
});

describe("grammar drill", () => {
  it("runs a choice and a cloze exercise", async () => {
    const answers: unknown[] = [];
    let drillBody: unknown = null;
    mockApi({
      "GET /api/grammar/gram%3Aakk": {
        id: "gram:akk",
        title_it: "Accusativo",
        title_en: "Accusative",
        level: "A1",
        requires: [],
        diagnostic_tags: {},
        reference_it: "Regola.",
        examples: [],
      },
      "POST /api/drills": ({ body }) => {
        drillBody = body;
        return {
          status: 201,
          body: {
            session_id: "d1",
            cards: [pending("c1", "choice"), pending("c2", "cloze")],
          },
        };
      },
      "POST /api/exercises/c1/prepare": {
        ...base,
        exercise_id: "c1",
        status: "ready",
        subtype: "choice",
        instructions: "Scegli la forma giusta.",
        prompt: { text: "Ich sehe ___ Hund.", options: ["der", "den", "dem"] },
        fallback_cards: [],
      },
      "POST /api/exercises/c2/prepare": {
        ...base,
        exercise_id: "c2",
        status: "ready",
        subtype: "cloze",
        instructions: "Completa.",
        prompt: { text: "Er kauft ___ Tisch." },
        fallback_cards: [],
      },
      "POST /api/sessions/d1/answers": ({ body }) => {
        answers.push(body);
        return {
          body: grade(
            answers.length === 1
              ? "Ich sehe den Hund."
              : "Er kauft einen Tisch.",
          ),
        };
      },
      "POST /api/evaluations/3/explain": {
        markdown: "x",
        examples: [],
        cached: false,
      },
    });
    renderDrill();
    const user = userEvent.setup();

    expect(
      await screen.findByRole("heading", { name: "Esercizi: Accusativo" }),
    ).toBeInTheDocument();
    expect(drillBody).toEqual({ item_id: "gram:akk" });
    expect(await screen.findByText(/Ich sehe .* Hund\./)).toHaveAttribute(
      "lang",
      "de",
    );
    await user.click(screen.getByRole("button", { name: "den" }));
    expect(await screen.findByText("Ich sehe den Hund.")).toBeInTheDocument();
    expect(screen.getByText("Bene.")).toBeInTheDocument();
    expect(answers[0]).toMatchObject({
      exercise_id: "c1",
      answer: { choice: 1 },
    });
    await user.click(screen.getByRole("button", { name: "Next" }));

    const field = await screen.findByLabelText("Your answer");
    expect(field.tagName).toBe("INPUT");
    await user.type(field, "einen{Enter}");
    expect(
      await screen.findByText("Er kauft einen Tisch."),
    ).toBeInTheDocument();
    expect(answers[1]).toMatchObject({
      exercise_id: "c2",
      answer: { text: "einen" },
    });
    await user.click(screen.getByRole("button", { name: "Finish" }));
    expect(
      await screen.findByRole("button", { name: "Another drill" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Back to the grammar sheet" }),
    ).toHaveAttribute("href", "/grammar/gram%3Aakk");
  });
});
