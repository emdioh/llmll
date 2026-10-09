import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { Learner } from "../api/client";
import { LearnerContext } from "../learnerContext";
import { AuthContext } from "../authContext";
import { LEARNER, STATS, mockApi } from "../test/mockApi";
import SettingsView from "./SettingsView";

const health = { status: "ok", version: "0.1.0", database: "ok" };

function renderView(setLearner = vi.fn()) {
  render(
    <LearnerContext.Provider
      value={{ learner: LEARNER as Learner, setLearner }}
    >
      <MemoryRouter>
        <SettingsView />
      </MemoryRouter>
    </LearnerContext.Provider>,
  );
  return setLearner;
}

describe("SettingsView stats and access", () => {
  it("shows stats numbers and the calibration table", async () => {
    mockApi({ "GET /api/health": health, "GET /api/stats": STATS });
    renderView();
    expect(await screen.findByText("Reviews, 7 days")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("87%")).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /80%–90%/ });
    expect(row).toHaveTextContent("85%");
    expect(row).toHaveTextContent("30");
  });

  it("handles empty calibration and null retention", async () => {
    mockApi({
      "GET /api/health": health,
      "GET /api/stats": {
        ...STATS,
        observed_retention: null,
        calibration: [],
      },
    });
    renderView();
    expect(
      await screen.findByText("Not enough reviews yet."),
    ).toBeInTheDocument();
  });

  it("offers logout only when auth is enabled", async () => {
    mockApi({ "GET /api/health": health, "GET /api/stats": STATS });
    const logout = vi.fn();
    const { unmount } = render(
      <AuthContext.Provider value={{ enabled: true, logout }}>
        <LearnerContext.Provider
          value={{ learner: LEARNER as Learner, setLearner: vi.fn() }}
        >
          <MemoryRouter>
            <SettingsView />
          </MemoryRouter>
        </LearnerContext.Provider>
      </AuthContext.Provider>,
    );
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Log out" }));
    expect(logout).toHaveBeenCalled();
    unmount();
    renderView();
    await screen.findByText("Reviews, 7 days");
    expect(
      screen.queryByRole("button", { name: "Log out" }),
    ).not.toBeInTheDocument();
  });
});

describe("SettingsView", () => {
  it("shows backend ok state", async () => {
    mockApi({ "GET /api/health": health });
    renderView();
    expect(
      await screen.findByText("Backend: ok (v0.1.0), database: ok"),
    ).toBeInTheDocument();
  });

  it("shows error state when fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down")),
    );
    renderView();
    expect(await screen.findByText(/Backend: unreachable/)).toBeInTheDocument();
  });

  it("validates bounds before sending anything", async () => {
    const fetchMock = mockApi({
      "GET /api/health": health,
      "GET /api/stats": STATS,
    });
    renderView();
    const user = userEvent.setup();
    const retention = screen.getByLabelText("Desired retention");
    await user.clear(retention);
    await user.type(retention, "0.99");
    const cap = screen.getByLabelText("Reviews per session (cap)");
    await user.clear(cap);
    await user.type(cap, "0");
    const perSession = screen.getByLabelText("New words per session");
    await user.clear(perSession);
    await user.type(perSession, "2.5");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("Must be between 0.7 and 0.97"),
    ).toBeInTheDocument();
    expect(screen.getByText("Must be between 1 and 500")).toBeInTheDocument();
    expect(screen.getByText("Must be a whole number")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.every(([u]) =>
        ["/api/health", "/api/stats", "/api/settings/llm"].includes(String(u)),
      ),
    ).toBe(true);
  });

  it("saves level and settings", async () => {
    let settingsBody: unknown = null;
    let learnerBody: unknown = null;
    mockApi({
      "GET /api/health": health,
      "PUT /api/learner": ({ body }) => {
        learnerBody = body;
        return { body: { ...LEARNER, level: "B1" } };
      },
      "PUT /api/settings": ({ body }) => {
        settingsBody = body;
        return { body: { ...LEARNER.settings, ...(body as object) } };
      },
    });
    const setLearner = renderView();
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Level"), "B1");
    const lemmas = screen.getByLabelText("New words per week");
    await user.clear(lemmas);
    await user.type(lemmas, "30");
    const slots = screen.getByLabelText("Written exercises per session");
    await user.clear(slots);
    await user.type(slots, "3");
    await user.selectOptions(screen.getByLabelText("Time zone"), "Europe/Rome");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
    expect(learnerBody).toEqual({ level: "B1" });
    expect(settingsBody).toEqual({
      weekly_new_lemmas: 30,
      weekly_new_grammar: 2,
      new_per_session: 5,
      production_slots: 3,
      review_cap: 15,
      desired_retention: 0.85,
      timezone: "Europe/Rome",
    });
    expect(setLearner).toHaveBeenCalledWith(
      expect.objectContaining({
        level: "B1",
        settings: expect.objectContaining({ weekly_new_lemmas: 30 }),
      }),
    );
  });

  it("shows a server error on save", async () => {
    mockApi({
      "GET /api/health": health,
      "PUT /api/settings": () => ({
        status: 422,
        body: { detail: [{ msg: "bad value" }] },
      }),
    });
    renderView();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("bad value")).toBeInTheDocument();
  });
});
