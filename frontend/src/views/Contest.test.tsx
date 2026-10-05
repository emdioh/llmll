import { MemoryRouter } from "react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { FlashcardAnswer, ProductionAnswer } from "../api/client";
import { mockApi } from "../test/mockApi";
import Feedback from "./cards/Feedback";
import ProductionFeedback from "./cards/ProductionFeedback";

const production: ProductionAnswer = {
  kind: "production",
  outcome: "major_errors",
  corrected_sentence: "Ich sehe den Hund.",
  errors: [],
  feedback: "",
  items: [
    {
      item_id: "gram:akk",
      label: "Accusativo",
      outcome: "error",
      needs_remediation: false,
    },
    {
      item_id: "lex:hund",
      label: "Hund",
      outcome: "assisted",
      needs_remediation: false,
    },
    {
      item_id: "lex:sehen",
      label: "sehen",
      outcome: "correct",
      needs_remediation: false,
    },
  ],
  evaluation_id: 7,
};

function contestResult(
  verdict: "accepted" | "rejected" | "partial",
  items: { item_id: string; label: string; outcome: string }[],
  rationale = "",
) {
  return {
    contest: {
      id: 1,
      evaluation_id: 7,
      item_ids: [],
      reason: null,
      status: "resolved",
      verdict,
      resolver: "accept_all",
      rationale,
      replacement_evaluation_id: 8,
      created_at: "2026-01-01T00:00:00Z",
      resolved_at: "2026-01-01T00:00:00Z",
    },
    evaluation_id: 8,
    items: items.map((i) => ({ ...i, previous: "error" })),
  };
}

function renderProduction() {
  render(
    <MemoryRouter>
      <ProductionFeedback answer="Ich sehe der Hund." result={production} />
    </MemoryRouter>,
  );
}

describe("contest: production feedback", () => {
  it("contests the whole answer with a reason and shows the result inline", async () => {
    let sent: unknown = null;
    mockApi({
      "POST /api/evaluations/7/contest": ({ body }) => {
        sent = body;
        return {
          body: contestResult("accepted", [
            { item_id: "gram:akk", label: "Accusativo", outcome: "correct" },
            { item_id: "lex:hund", label: "Hund", outcome: "correct" },
          ]),
        };
      },
    });
    renderProduction();
    const user = userEvent.setup();
    expect(screen.getByText("Needs work")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Secondo me era giusto" }),
    );
    await user.type(screen.getByLabelText(/Why was it right/), "colloquiale");
    await user.click(screen.getByRole("button", { name: "Send contest" }));

    expect(
      await screen.findByText("Ok, conteggiato come corretto."),
    ).toBeInTheDocument();
    expect(sent).toEqual({ item_ids: [], reason: "colloquiale" });
    expect(screen.getByText("Correct", { selector: "p" })).toBeInTheDocument();
    const items = within(screen.getByRole("list", { name: "Items" }));
    expect(items.getAllByText(/Correct/)).toHaveLength(3);
    // one contest only
    expect(
      screen.queryByRole("button", { name: "Secondo me era giusto" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("contest")).not.toBeInTheDocument();
  });

  it("contests a single item and updates only its outcome", async () => {
    let sent: unknown = null;
    mockApi({
      "POST /api/evaluations/7/contest": ({ body }) => {
        sent = body;
        return {
          body: {
            ...contestResult("accepted", [
              { item_id: "gram:akk", label: "Accusativo", outcome: "correct" },
            ]),
            contest: {
              ...contestResult("accepted", []).contest,
              item_ids: ["gram:akk"],
            },
          },
        };
      },
    });
    renderProduction();
    const user = userEvent.setup();
    // only items with error/assisted outcome get a contest action
    expect(screen.getAllByRole("button", { name: /^Contest / })).toHaveLength(
      2,
    );
    await user.click(
      screen.getByRole("button", { name: "Contest Accusativo" }),
    );
    expect(screen.getByText("Contesting: Accusativo")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send contest" }));
    expect(
      await screen.findByText("Ok, conteggiato come corretto."),
    ).toBeInTheDocument();
    expect(sent).toEqual({ item_ids: ["gram:akk"], reason: null });
    expect(screen.getByText("Needs work")).toBeInTheDocument();
    const items = screen.getByRole("list", { name: "Items" });
    expect(
      within(items).getByText(/Accusativo/).parentElement,
    ).toHaveTextContent("Accusativo: Correct");
    expect(within(items).getByText(/Hund/).parentElement).toHaveTextContent(
      "Hund: Almost",
    );
  });

  it("shows the resolver's rationale when the contest is rejected", async () => {
    mockApi({
      "POST /api/evaluations/7/contest": {
        ...contestResult(
          "rejected",
          [{ item_id: "gram:akk", label: "Accusativo", outcome: "error" }],
          "Dopo sehen serve l'accusativo.",
        ),
      },
    });
    renderProduction();
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "Secondo me era giusto" }),
    );
    await user.click(screen.getByRole("button", { name: "Send contest" }));
    expect(
      await screen.findByText("Dopo sehen serve l'accusativo."),
    ).toBeInTheDocument();
    expect(screen.getByText("Needs work")).toBeInTheDocument();
  });

  it("reports 409 as already contested and lets the form be cancelled", async () => {
    mockApi({
      "POST /api/evaluations/7/contest": () => ({
        status: 409,
        body: { detail: "Evaluation was superseded" },
      }),
    });
    renderProduction();
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "Secondo me era giusto" }),
    );
    await user.click(screen.getByRole("button", { name: "Send contest" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Already contested",
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(
      screen.getByRole("button", { name: "Secondo me era giusto" }),
    ).toBeInTheDocument();
  });
});

describe("contest: flashcard feedback", () => {
  const flash: FlashcardAnswer = {
    kind: "flashcard",
    outcome: "error",
    expected: {
      text: "der Tisch",
      lemma: "Tisch",
      article: "der",
      translation_it: "tavolo",
    } as FlashcardAnswer["expected"],
    diagnostic_tags: [],
    feedback_it: "",
    memory: null,
    evaluation_id: 12,
  };

  it("contests a flashcard answer", async () => {
    mockApi({
      "POST /api/evaluations/12/contest": {
        ...contestResult("accepted", [
          { item_id: "lex:tisch", label: "Tisch", outcome: "correct" },
        ]),
      },
    });
    render(<Feedback result={flash} />);
    const user = userEvent.setup();
    expect(screen.getByText("Incorrect")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Secondo me era giusto" }),
    );
    await user.click(screen.getByRole("button", { name: "Send contest" }));
    expect(
      await screen.findByText("Ok, conteggiato come corretto."),
    ).toBeInTheDocument();
    expect(screen.getByText("Correct")).toBeInTheDocument();
  });

  it("offers no contest for intro cards or correct answers", () => {
    const { rerender } = render(
      <Feedback result={{ ...flash, evaluation_id: null }} />,
    );
    expect(
      screen.queryByRole("button", { name: "Secondo me era giusto" }),
    ).not.toBeInTheDocument();
    rerender(<Feedback result={{ ...flash, outcome: "correct" }} />);
    expect(
      screen.queryByRole("button", { name: "Secondo me era giusto" }),
    ).not.toBeInTheDocument();
  });
});
