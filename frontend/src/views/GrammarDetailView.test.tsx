import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import GrammarDetailView from "./GrammarDetailView";

describe("GrammarDetailView", () => {
  it("asks a question and renders the markdown answer with examples", async () => {
    let asked: unknown = null;
    mockApi({
      "GET /api/grammar/gram%3Aarticles": {
        id: "gram:articles",
        title_it: "Articoli",
        title_en: "Articles",
        level: "A1",
        requires: [],
        diagnostic_tags: {},
        reference_it: "Regola.",
        examples: [],
      },
      "POST /api/grammar/gram%3Aarticles/explain": ({ body }) => {
        asked = body;
        return {
          body: {
            markdown: "Si usa **den** all'accusativo.",
            examples: [
              { de: "Ich sehe den Hund.", translation: "Vedo il cane." },
            ],
            cached: false,
          },
        };
      },
    });
    render(
      <MemoryRouter initialEntries={["/grammar/gram%3Aarticles"]}>
        <Routes>
          <Route path="/grammar/:id" element={<GrammarDetailView />} />
        </Routes>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.type(
      await screen.findByLabelText("Fai una domanda"),
      "Quando uso den?",
    );
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect((await screen.findByText("den")).tagName).toBe("STRONG");
    expect(screen.getByText("Ich sehe den Hund.")).toBeInTheDocument();
    expect(screen.getByText("Vedo il cane.")).toBeInTheDocument();
    expect(asked).toEqual({ question: "Quando uso den?" });
  });

  it("renders a markdown table", async () => {
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
    expect(screen.queryByText("Il tavolo è grande.")).not.toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/grammar/gram%3Aarticles",
    );
  });
});
