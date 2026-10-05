import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import CorpusView from "./CorpusView";

const list = {
  total: 1,
  items: [
    {
      id: "lex:bank#money",
      kind: "lemma",
      level: "A2",
      label: "die Bank",
      translation_it: "banca",
      status: "introduced",
      memory: [
        {
          facet: "recognition",
          due: "2999-01-01T00:00:00Z",
          mastery: 0.6,
          stability: 2,
        },
      ],
    },
  ],
};

describe("CorpusView", () => {
  it("lists items and sends filters as query params", async () => {
    const fetchMock = mockApi({ "GET /api/items": list });
    render(
      <MemoryRouter>
        <CorpusView />
      </MemoryRouter>,
    );
    const link = await screen.findByRole("link", { name: /die Bank/ });
    expect(link).toHaveAttribute("href", "/corpus/lex%3Abank%23money");
    expect(screen.getByRole("meter", { name: "Mastery" })).toHaveAttribute(
      "aria-valuenow",
      "60",
    );

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Level"), "A2");
    await user.selectOptions(screen.getByLabelText("Kind"), "lemma");
    await user.type(screen.getByLabelText("Search"), "bank");

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([u]) => String(u));
      expect(urls).toContain(
        "/api/items?kind=lemma&level=A2&q=bank&limit=50&offset=0",
      );
    });
    // debounced: no request for partial search terms
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls.some((u) => u.includes("q=ba&") || u.includes("q=b&"))).toBe(
      false,
    );
  });
});
