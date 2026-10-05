import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { Learner } from "../api/client";
import { LearnerContext } from "../learnerContext";
import { LEARNER, mockApi } from "../test/mockApi";
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
    const fetchMock = mockApi({ "GET /api/health": health });
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
      fetchMock.mock.calls.every(([u]) => String(u) === "/api/health"),
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
