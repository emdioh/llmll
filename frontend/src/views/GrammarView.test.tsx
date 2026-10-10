import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import GrammarView from "./GrammarView";

const base = {
  status: null,
  state: null,
  mastery: null,
  introduced_at: null,
  last_practiced: null,
  due: null,
};

describe("GrammarView", () => {
  it("shows the learner status of each grammar point", async () => {
    mockApi({
      "GET /api/grammar": [
        {
          ...base,
          id: "gram:a",
          title_it: "Nuovo punto",
          level: "A2",
          status: "unseen",
          state: "new",
        },
        {
          ...base,
          id: "gram:b",
          title_it: "Articoli",
          level: "A1",
          status: "presumed_known",
          state: "presumed_known",
        },
        {
          ...base,
          id: "gram:c",
          title_it: "Casi",
          level: "A2",
          status: "introduced",
          state: "learning",
          mastery: 0.6,
          introduced_at: "2026-10-01T10:00:00Z",
          last_practiced: "2026-10-05T10:00:00Z",
        },
      ],
    });
    render(
      <MemoryRouter>
        <GrammarView />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Nuovo")).toBeInTheDocument();
    expect(screen.getByText("Già noto")).toBeInTheDocument();
    expect(screen.getByText(/Studiato il 1 ott/)).toBeInTheDocument();
    expect(screen.getByText(/ultimo esercizio 5 ott/)).toBeInTheDocument();
    const meters = screen.getAllByRole("meter");
    expect(meters).toHaveLength(1);
    expect(meters[0]).toHaveAttribute("aria-valuenow", "60");
  });
});
