import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App";
import {
  ACTIVITY,
  ANSWER,
  CARD,
  FORECAST,
  GRAMMAR_ROW,
  LEVELS,
  SUMMARY,
  WORD_ROW,
  itemList,
  progressItem,
} from "../../test/progressFixtures";
import { AUTH_OFF, LEARNER, mockApi } from "../../test/mockApi";
import { ReadingDetailView, SessionDetailView } from "./History";
import ItemDetail from "./ItemDetail";
import ProgressView from "./ProgressView";

afterEach(() => vi.unstubAllGlobals());

const overviewRoutes = {
  "GET /api/progress/summary": SUMMARY,
  "GET /api/progress/activity": ACTIVITY,
  "GET /api/progress/levels": LEVELS,
  "GET /api/progress/forecast": FORECAST,
};

function renderProgress(path = "/progress") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/progress" element={<ProgressView />} />
      </Routes>
    </MemoryRouter>,
  );
}

function urls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls.map(([u]) => String(u));
}

describe("progress overview", () => {
  it("renders streak, week comparison, retention, forecast, heatmap and accuracy", async () => {
    const fetchMock = mockApi({
      ...overviewRoutes,
      "GET /api/progress/items": itemList([GRAMMAR_ROW]),
    });
    renderProgress();
    expect(
      await screen.findByLabelText("Current streak in days"),
    ).toHaveTextContent("4");
    expect(screen.getByText(/Longest 9/)).toBeInTheDocument();
    expect(screen.getByText("studied today", { exact: false })).toBeVisible();
    const table = screen.getByRole("table", { name: /Last 7 days/ });
    expect(
      within(table).getByRole("row", { name: /Study days/ }),
    ).toHaveTextContent("53+2");
    expect(screen.getByText("87%")).toBeInTheDocument();
    expect(screen.getByText(/below target/)).toBeInTheDocument();
    // charts
    expect(await screen.findAllByTestId("heat-cell")).toHaveLength(7);
    expect(screen.getByText("Reviews due, next 14 days")).toBeInTheDocument();
    expect(
      screen.getByLabelText(
        "Weekly accuracy, flashcards and written exercises",
        {
          exact: false,
        },
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Words by CEFR level: A1/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Grammar by CEFR level: A1/ }),
    ).toBeInTheDocument();
    // heat cell readout on tap
    await userEvent.click(screen.getAllByTestId("heat-cell")[4]!);
    expect(screen.getByText(/55 reviews, 3 exercises/)).toBeInTheDocument();
    expect(urls(fetchMock)).toContain("/api/progress/activity?days=140");
  });

  it("loads the tabs lazily and keeps the active tab in the URL", async () => {
    const fetchMock = mockApi({
      ...overviewRoutes,
      "GET /api/progress/items": ({ url }) => ({
        body: itemList(
          url.searchParams.get("kind") === "lemma" ? [WORD_ROW] : [GRAMMAR_ROW],
        ),
      }),
      "GET /api/progress/history": {
        total: 0,
        limit: 20,
        offset: 0,
        items: [],
      },
    });
    renderProgress();
    // grammar is the default tab
    const dativ = await screen.findByRole("link", { name: /^Dativ/ });
    expect(dativ).toHaveAttribute("href", "/progress/items/gram%3Adativ");
    expect(
      screen.getByRole("link", { name: "Reference: Dativ" }),
    ).toHaveAttribute("href", "/grammar/gram%3Adativ");
    expect(dativ).toHaveTextContent("dativ feminine ×4");
    expect(urls(fetchMock).some((u) => u.includes("kind=lemma"))).toBe(false);
    expect(urls(fetchMock).some((u) => u.includes("history"))).toBe(false);

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Words" }));
    expect(
      await screen.findByRole("link", { name: /die Bank/ }),
    ).toHaveAttribute("href", "/progress/items/lex%3Abank%23money");
    expect(screen.getByRole("tab", { name: "Words" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.type(screen.getByLabelText("Search"), "bank");
    await user.selectOptions(screen.getByLabelText("Level"), "A2");
    await user.selectOptions(screen.getByLabelText("Sort"), "weakest");
    await waitFor(() =>
      expect(urls(fetchMock)).toContain(
        "/api/progress/items?kind=lemma&sort=weakest&level=A2&q=bank&limit=30&offset=0",
      ),
    );
    await user.click(screen.getByRole("tab", { name: "History" }));
    expect(await screen.findByText(/No lessons yet/)).toBeInTheDocument();
  });

  it("opens the words tab from ?tab=words", async () => {
    mockApi({
      ...overviewRoutes,
      "GET /api/progress/items": itemList([WORD_ROW]),
    });
    renderProgress("/progress?tab=words");
    expect(await screen.findByRole("link", { name: /die Bank/ })).toBeVisible();
    expect(screen.getAllByRole("meter")).toHaveLength(2);
  });
});

function renderDetail(id = "gram%3Adativ") {
  render(
    <MemoryRouter initialEntries={[`/progress/items/${id}`]}>
      <Routes>
        <Route path="/progress/items/:id" element={<ItemDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("progress item detail", () => {
  it("shows the trajectory with error markers, ranked errors and recent answers", async () => {
    mockApi({ "GET /api/progress/items/gram%3Adativ": progressItem() });
    renderDetail();
    expect(
      await screen.findByRole("heading", { name: "Dativ" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: /Mastery over time/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("error-marker")).toHaveLength(1);
    const tags = screen.getByRole("list", { name: "Error tags" });
    expect(tags).toHaveTextContent("dativ feminine");
    expect(tags).toHaveTextContent("×4");
    // recent answer: the error is highlighted and the correction shown on tap
    const card = screen.getByTestId("answer-card");
    expect(card).toHaveTextContent("Dai il libro alla donna.");
    const err = within(card).getByRole("button", { name: "dem Frau" });
    await userEvent.click(err);
    expect(card).toHaveTextContent("der Frau");
    expect(card).toHaveTextContent("Ich gebe der Frau das Buch.");
    // read-only: no contest button
    expect(within(card).queryByText(/Secondo me/)).toBeNull();
    expect(
      screen.getByRole("link", { name: "Open the grammar reference" }),
    ).toHaveAttribute("href", "/grammar/gram%3Adativ");
  });

  it("explains from the latest mistake and queues practice", async () => {
    let explained: unknown = null;
    mockApi({
      "GET /api/progress/items/gram%3Adativ": progressItem(),
      "POST /api/evaluations/21/explain": ({ body }) => {
        explained = body;
        return {
          body: { markdown: "Il **dativo** ...", examples: [], cached: false },
        };
      },
      "POST /api/progress/items/gram%3Adativ/practice": {
        item_id: "gram:dativ",
        queued: true,
        already_queued: false,
        production_slots: 2,
      },
    });
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Spiegami" }));
    expect(await screen.findByText("dativo")).toBeInTheDocument();
    expect(explained).toEqual({ item_id: "gram:dativ" });
    await user.click(screen.getByRole("button", { name: "Practice this" }));
    expect(await screen.findByText(/Queued: your next session/)).toBeVisible();
  });

  it("reports practice refused (409) and the 0-slot case", async () => {
    let calls = 0;
    mockApi({
      "GET /api/progress/items/gram%3Adativ": progressItem(),
      "POST /api/progress/items/gram%3Adativ/practice": () =>
        ++calls === 1
          ? { status: 409, body: { detail: "Item not met yet" } }
          : {
              body: {
                item_id: "gram:dativ",
                queued: false,
                already_queued: false,
                production_slots: 0,
              },
            },
    });
    renderDetail();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Practice this" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "not met this item",
    );
    await user.click(screen.getByRole("button", { name: "Practice this" }));
    expect(await screen.findByText(/is 0 in Settings/)).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("explains grammar without a mistake through the grammar endpoint and shows a queued state", async () => {
    let asked: unknown = null;
    mockApi({
      "GET /api/progress/items/gram%3Adativ": progressItem({
        recent_answers: [],
        practice_queued: true,
      }),
      "POST /api/grammar/gram%3Adativ/explain": ({ body }) => {
        asked = body;
        return {
          body: { markdown: "Spiegazione", examples: [], cached: false },
        };
      },
    });
    renderDetail();
    await userEvent.click(
      await screen.findByRole("button", { name: "Spiegami" }),
    );
    expect(await screen.findByText("Spiegazione")).toBeVisible();
    expect(asked).toHaveProperty("question");
    expect(
      screen.getByRole("button", { name: "Practice this" }),
    ).toBeDisabled();
  });
});

const SESSION = {
  kind: "session",
  id: "s1",
  started_at: "2026-10-07T09:00:00Z",
  ended_at: "2026-10-07T09:08:00Z",
  duration_minutes: 8,
  cards_answered: 2,
  correct_rate: 0.5,
  new_items: 1,
  cards: [
    CARD,
    {
      ...ANSWER,
      contest: {
        id: 1,
        status: "resolved",
        verdict: "rejected",
        reason: null,
        rationale: "The dative needs der.",
        created_at: "2026-10-07T10:05:00Z",
      },
    },
  ],
};

describe("history", () => {
  it("lists lessons and renders a session's answers read-only", async () => {
    mockApi({
      "GET /api/progress/history": {
        total: 2,
        limit: 20,
        offset: 0,
        items: [
          {
            kind: "session",
            id: "s1",
            started_at: "2026-10-07T09:00:00Z",
            ended_at: null,
            duration_minutes: 8,
            cards_answered: 2,
            correct_rate: 0.5,
            new_items: 1,
            title: null,
            words_looked_up: null,
          },
          {
            kind: "reading",
            id: "5",
            started_at: "2026-10-06T09:00:00Z",
            ended_at: null,
            duration_minutes: 7,
            cards_answered: 1,
            correct_rate: 1,
            new_items: 0,
            title: "Der Tisch",
            words_looked_up: 3,
          },
        ],
      },
      "GET /api/progress/history/session/s1": SESSION,
    });
    render(
      <MemoryRouter initialEntries={["/progress?tab=history"]}>
        <Routes>
          <Route path="/progress" element={<ProgressView />} />
          <Route
            path="/progress/history/session/:id"
            element={<SessionDetailView />}
          />
        </Routes>
      </MemoryRouter>,
    );
    // overview endpoints are not mocked here: they show errors but the tab still works
    const reading = await screen.findByRole("link", {
      name: /Reading: Der Tisch/,
    });
    expect(reading).toHaveAttribute("href", "/progress/history/reading/5");
    await userEvent.click(screen.getByRole("link", { name: /Review session/ }));
    const cards = await screen.findAllByTestId("answer-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("Your answer: die Bank");
    expect(cards[1]).toHaveTextContent("Ich dem Frau");
    expect(cards[1]).toHaveTextContent("Contested: rejected");
    expect(screen.queryByText(/Secondo me/)).toBeNull();
    expect(screen.queryByRole("button", { name: /contest/i })).toBeNull();
  });

  it("renders a reading with lookups and its summary exercise", async () => {
    mockApi({
      "GET /api/progress/history/reading/5": {
        kind: "reading",
        id: "5",
        text_id: 2,
        title: "Der Tisch",
        level: "A1",
        body: "Der Tisch ist groß.\n\nDie Katze schläft.",
        source_title: "generated",
        source_url: null,
        started_at: "2026-10-06T09:00:00Z",
        ended_at: "2026-10-06T09:07:00Z",
        duration_minutes: 7,
        lookups: [
          {
            token_index: 1,
            word: "Tisch",
            lemma: "Tisch",
            item_id: "lex:tisch",
            label: "der Tisch",
          },
        ],
        summary: { ...ANSWER, type: "production", subtype: "summary" },
      },
    });
    render(
      <MemoryRouter initialEntries={["/progress/history/reading/5"]}>
        <Routes>
          <Route
            path="/progress/history/reading/:id"
            element={<ReadingDetailView />}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("heading", { name: "Der Tisch" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Die Katze schläft.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "der Tisch" })).toHaveAttribute(
      "href",
      "/progress/items/lex%3Atisch",
    );
    expect(screen.getByTestId("answer-card")).toHaveTextContent("Ich dem Frau");
  });
});

describe("app wiring", () => {
  function renderApp(path: string) {
    render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    );
  }

  it("sets the browser time zone once when none was chosen", async () => {
    const spy = vi
      .spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions")
      .mockReturnValue({
        timeZone: "Europe/Rome",
      } as Intl.ResolvedDateTimeFormatOptions);
    let put: unknown = null;
    const fetchMock = mockApi({
      "GET /api/auth/status": AUTH_OFF,
      "GET /api/learner": LEARNER,
      "PUT /api/settings": ({ body }) => {
        put = body;
        return { body: { ...LEARNER.settings, timezone: "Europe/Rome" } };
      },
      "GET /api/queue": {
        budget: { lemmas_left: 0, grammar_left: 0, backlog: 0 },
        next: [],
      },
    });
    renderApp("/");
    await screen.findByRole("heading", { name: "Session" });
    await waitFor(() => expect(put).toEqual({ timezone: "Europe/Rome" }));
    expect(
      fetchMock.mock.calls.filter(([, i]) => i?.method === "PUT"),
    ).toHaveLength(1);
    spy.mockRestore();
  });

  it("does not touch the time zone when it already matches or was chosen", async () => {
    const spy = vi
      .spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions")
      .mockReturnValue({
        timeZone: "Europe/Rome",
      } as Intl.ResolvedDateTimeFormatOptions);
    const fetchMock = mockApi({
      "GET /api/auth/status": AUTH_OFF,
      "GET /api/learner": {
        ...LEARNER,
        settings: { ...LEARNER.settings, timezone: "America/New_York" },
      },
      "GET /api/queue": {
        budget: { lemmas_left: 0, grammar_left: 0, backlog: 0 },
        next: [],
      },
    });
    renderApp("/");
    await screen.findByRole("heading", { name: "Session" });
    expect(
      fetchMock.mock.calls.filter(([, i]) => i?.method === "PUT"),
    ).toHaveLength(0);
    spy.mockRestore();
  });

  it("has exactly five navigation entries and redirects the old corpus URLs", async () => {
    mockApi({
      "GET /api/auth/status": AUTH_OFF,
      "GET /api/learner": LEARNER,
      ...overviewRoutes,
      "GET /api/progress/items": ({ url }) => ({
        body: itemList(
          url.searchParams.get("kind") === "lemma" ? [WORD_ROW] : [],
        ),
      }),
      "GET /api/progress/items/lex%3Aa": progressItem(
        {},
        { id: "lex:a", kind: "lemma", label: "die Aktie" },
      ),
    });
    renderApp("/corpus");
    expect(await screen.findByRole("link", { name: /die Bank/ })).toBeVisible();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(
      within(nav)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["Session", "Reading", "Progress", "Grammar", "Settings"]);
  });

  it("redirects /corpus/:id to the progress detail", async () => {
    mockApi({
      "GET /api/auth/status": AUTH_OFF,
      "GET /api/learner": LEARNER,
      "GET /api/progress/items/lex%3Aa": progressItem(
        {},
        { id: "lex:a", kind: "lemma", label: "die Aktie" },
      ),
    });
    renderApp("/corpus/lex%3Aa");
    expect(
      await screen.findByRole("heading", { name: "die Aktie" }),
    ).toBeInTheDocument();
  });
});
