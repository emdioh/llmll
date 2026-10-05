import { MemoryRouter, Route, Routes } from "react-router";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Learner } from "../api/client";
import { LearnerContext } from "../learnerContext";
import { LEARNER, mockApi } from "../test/mockApi";
import ItemDetailView from "./ItemDetailView";
import SessionView from "./SessionView";

const queue = {
  budget: { lemmas_left: 12, grammar_left: 1, backlog: 4 },
  next: [
    { item_id: "lex:a", label: "die Aktie", source: "optin", kind: "lemma" },
    { item_id: "lex:b", label: "der Baum", source: "article", kind: "lemma" },
    {
      item_id: "gram:c",
      label: "Perfekt",
      source: "wordlist",
      kind: "grammar",
    },
  ],
};

describe("queue summary", () => {
  it("shows the weekly budget, backlog and a collapsible coming-up list", async () => {
    mockApi({ "GET /api/queue": queue });
    render(
      <LearnerContext.Provider
        value={{ learner: LEARNER as Learner, setLearner: () => {} }}
      >
        <MemoryRouter>
          <SessionView />
        </MemoryRouter>
      </LearnerContext.Provider>,
    );
    expect(
      await screen.findByText(
        "This week: 12/20 new words, 1/2 grammar points left.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Backlog: 4 cards due")).toBeInTheDocument();
    expect(screen.queryByText("die Aktie")).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Coming up (3)" }));
    expect(screen.getByText("die Aktie")).toBeInTheDocument();
    for (const source of ["optin", "article", "wordlist"])
      expect(screen.getByText(source)).toHaveClass("badge");
  });
});

function item(status: string, source: string | null = null) {
  return {
    id: "lex:a",
    kind: "lemma",
    level: "A2",
    label: "die Aktie",
    translation_it: "azione",
    payload: {},
    interference: {},
    requires: [],
    frequency_zipf: 3,
    suspended: false,
    status,
    candidate_source: source,
    introduced_at: null,
    memory: [],
  };
}

function renderItem() {
  render(
    <MemoryRouter initialEntries={["/corpus/lex%3Aa"]}>
      <Routes>
        <Route path="/corpus/:id" element={<ItemDetailView />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("opt-in / opt-out", () => {
  it("opts an unseen item out, then back in", async () => {
    mockApi({
      "GET /api/items/lex%3Aa": item("unseen"),
      "POST /api/items/lex%3Aa/optout": {
        item_id: "lex:a",
        status: "suspended",
        candidate_source: null,
      },
      "POST /api/items/lex%3Aa/optin": {
        item_id: "lex:a",
        status: "candidate",
        candidate_source: "optin",
      },
    });
    renderItem();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Don't teach me this" }),
    );
    expect(
      await screen.findByText("You asked not to be taught this."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Don't teach me this" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Learn this" }));
    expect(
      await screen.findByText("Queued: you chose to learn this."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Don't teach me this" }),
    ).toBeInTheDocument();
  });

  it("shows no opt buttons for learned items", async () => {
    mockApi({ "GET /api/items/lex%3Aa": item("introduced") });
    renderItem();
    await screen.findByRole("heading", { name: "die Aktie" });
    expect(screen.queryByRole("button", { name: "Learn this" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Don't teach me this" }),
    ).toBeNull();
  });

  it("shows the server error when opt-out is refused", async () => {
    mockApi({
      "GET /api/items/lex%3Aa": item("candidate", "wordlist"),
      "POST /api/items/lex%3Aa/optout": () => ({
        status: 409,
        body: { detail: "Item is already introduced; it cannot be opted out" },
      }),
    });
    renderItem();
    await userEvent.click(
      await screen.findByRole("button", { name: "Don't teach me this" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("already");
  });
});
