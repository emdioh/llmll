import { MemoryRouter } from "react-router";
import { render, screen } from "@testing-library/react";
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

const expected = {
  text: "der Tisch",
  lemma: "Tisch",
  article: "der",
  translation_it: "tavolo",
};

describe("SessionView", () => {
  it("runs one card of each type through to the summary", async () => {
    const answers: unknown[] = [];
    mockApi({
      "POST /api/sessions": () => ({
        status: 201,
        body: {
          session_id: "s1",
          cards: [
            {
              exercise_id: "e1",
              type: "flashcard_intro",
              item_id: "lex:tisch",
              prompt: {
                lemma: "Tisch",
                article: "der",
                plural: "Tische",
                translation_it: "tavolo",
                translation_en: "table",
                interference_note: "In italiano è maschile.",
              },
            },
            {
              exercise_id: "e2",
              type: "flashcard_recognition",
              item_id: "lex:haus",
              prompt: {
                de: "das Haus",
                options: ["casa", "gatto", "cane", "pane"],
              },
            },
            {
              exercise_id: "e3",
              type: "flashcard_production",
              item_id: "lex:tisch",
              hint: "T _ _ _ _ (5)",
              prompt: { it: "tavolo", pos: "noun", needs_article: true },
            },
          ],
        },
      }),
      "POST /api/sessions/s1/answers": ({ body }) => {
        answers.push(body);
        const b = body as { exercise_id: string };
        if (b.exercise_id === "e1")
          return {
            body: {
              outcome: "correct",
              expected,
              diagnostic_tags: [],
              feedback_it: "",
              memory: null,
            },
          };
        if (b.exercise_id === "e2")
          return {
            body: {
              outcome: "correct",
              expected: {
                ...expected,
                text: "das Haus",
                translation_it: "casa",
              },
              diagnostic_tags: [],
              feedback_it: "Bene.",
              memory: null,
            },
          };
        return {
          body: {
            outcome: "error",
            expected,
            diagnostic_tags: ["gender"],
            feedback_it: "Attenzione: in tedesco è *der* Tisch.",
            memory: null,
          },
        };
      },
    });

    renderView();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start session" }));

    // intro
    expect(await screen.findByText("New word")).toBeInTheDocument();
    expect(screen.getByText("In italiano è maschile.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Got it" }));

    // recognition
    expect(await screen.findByText("das Haus")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "casa" }));
    expect(await screen.findByText("Correct")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));

    // production
    await user.click(await screen.findByRole("button", { name: "Hint" }));
    expect(screen.getByText(/T _ _ _ _/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Your answer"), "die Tisch");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText("Incorrect")).toBeInTheDocument();
    expect(screen.getByText("der Tisch")).toBeInTheDocument();
    expect(screen.getByText("der").tagName).toBe("EM");
    await user.click(screen.getByRole("button", { name: "Finish" }));

    // summary
    expect(
      await screen.findByRole("heading", { name: "Session complete" }),
    ).toBeInTheDocument();
    const stat = (name: string) =>
      screen.getByText(name).parentElement?.querySelector("dd")?.textContent;
    expect(stat("Reviewed")).toBe("2");
    expect(stat("New")).toBe("1");
    expect(stat("Errors")).toBe("1");

    expect(answers[0]).toMatchObject({
      exercise_id: "e1",
      answer: {},
      used_hint: false,
    });
    expect(answers[1]).toMatchObject({ answer: { choice: 0 } });
    expect(answers[2]).toMatchObject({
      answer: { text: "die Tisch" },
      used_hint: true,
    });
    expect(typeof (answers[2] as { duration_ms: unknown }).duration_ms).toBe(
      "number",
    );
  });

  it("handles an empty session", async () => {
    mockApi({
      "POST /api/sessions": () => ({
        status: 201,
        body: { session_id: "s0", cards: [] },
      }),
    });
    renderView();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    expect(await screen.findByText(/Nothing is due/)).toBeInTheDocument();
  });

  it("inserts umlauts at the cursor", async () => {
    mockApi({
      "POST /api/sessions": () => ({
        status: 201,
        body: {
          session_id: "s2",
          cards: [
            {
              exercise_id: "e1",
              type: "flashcard_production",
              item_id: "lex:schoen",
              prompt: { it: "bello", pos: "adj", needs_article: false },
            },
          ],
        },
      }),
    });
    renderView();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start session" }));
    const input = await screen.findByLabelText<HTMLInputElement>("Your answer");
    expect(input).toHaveAttribute("lang", "de");
    expect(input).toHaveAttribute("autocapitalize", "off");
    expect(input).toHaveAttribute("autocorrect", "off");
    expect(input).toHaveAttribute("spellcheck", "false");

    await user.type(input, "schn");
    input.setSelectionRange(3, 3); // schn -> sch|n
    await user.click(screen.getByRole("button", { name: "Insert ö" }));
    expect(input.value).toBe("schön");
    await user.click(screen.getByRole("button", { name: "Insert ß" }));
    expect(input.value).toBe("schößn");
  });
});
