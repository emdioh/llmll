import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import GrammarDetailView from "./GrammarDetailView";

describe("GrammarDetailView", () => {
  it("renders a markdown table and examples", async () => {
    const fetchMock = mockApi({
      "GET /api/grammar/gram%3Aarticles": {
        id: "gram:articles",
        title_it: "Articoli",
        title_en: "Articles",
        level: "A1",
        requires: [],
        diagnostic_tags: {},
        reference_it:
          "Regola.\n\n| Caso | maschile |\n|---|---|\n| Nom | der |\n| Akk | den |\n",
        examples: [{ de: "Der Tisch ist groß.", it: "Il tavolo è grande." }],
      },
    });
    render(
      <MemoryRouter initialEntries={["/grammar/gram%3Aarticles"]}>
        <Routes>
          <Route path="/grammar/:id" element={<GrammarDetailView />} />
        </Routes>
      </MemoryRouter>,
    );
    const table = await screen.findByRole("table");
    expect(
      within(table).getByRole("columnheader", { name: "Caso" }),
    ).toBeInTheDocument();
    expect(
      within(table).getByRole("cell", { name: "den" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Il tavolo è grande.")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/grammar/gram%3Aarticles",
    );
  });
});
