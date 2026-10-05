import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import App from "./App";
import { LEARNER, STATS, mockApi } from "./test/mockApi";

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

const locked = { auth: "enabled", authenticated: false };
const unlocked = { auth: "enabled", authenticated: true };

describe("AuthGate", () => {
  it("shows the login screen, rejects a wrong token, accepts the right one", async () => {
    let authed = false;
    const calls: string[] = [];
    mockApi({
      "GET /api/auth/status": () => ({ body: authed ? unlocked : locked }),
      "POST /api/auth/login": ({ body }) => {
        calls.push((body as { token: string }).token);
        if ((body as { token: string }).token !== "good")
          return { status: 401, body: { detail: "Invalid token" } };
        authed = true;
        return { body: unlocked };
      },
      "GET /api/learner": LEARNER,
      "GET /api/grammar": [],
      "GET /api/queue": { budget: {}, next: [] },
    });
    renderApp();
    const field = await screen.findByLabelText("Access token");
    expect(field).toHaveAttribute("type", "password");
    const user = userEvent.setup();
    await user.type(field, "bad");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Wrong access token")).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText("Access token"));
    await user.type(screen.getByLabelText("Access token"), "good");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByRole("navigation", { name: "Main" }),
    ).toBeInTheDocument();
    expect(calls).toEqual(["bad", "good"]);
  });

  it("goes straight to the app when authenticated or auth is disabled", async () => {
    mockApi({
      "GET /api/auth/status": unlocked,
      "GET /api/learner": LEARNER,
      "GET /api/grammar": [],
      "GET /api/queue": { budget: {}, next: [] },
    });
    renderApp("/grammar");
    expect(
      await screen.findByRole("heading", { name: "Grammar" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
  });

  it("returns to the login screen on any 401", async () => {
    mockApi({
      "GET /api/auth/status": unlocked,
      "GET /api/learner": LEARNER,
      "GET /api/grammar": () => ({ status: 401, body: { detail: "no" } }),
    });
    renderApp("/grammar");
    expect(await screen.findByLabelText("Access token")).toBeInTheDocument();
  });

  it("logs out from Settings", async () => {
    let loggedOut = false;
    mockApi({
      "GET /api/auth/status": () => ({ body: loggedOut ? locked : unlocked }),
      "POST /api/auth/logout": () => {
        loggedOut = true;
        return { body: locked };
      },
      "GET /api/learner": LEARNER,
      "GET /api/health": { status: "ok", version: "1", database: "ok" },
      "GET /api/stats": STATS,
    });
    renderApp("/settings");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Log out" }));
    expect(await screen.findByLabelText("Access token")).toBeInTheDocument();
    expect(loggedOut).toBe(true);
  });

  it("reports a throttled login", async () => {
    mockApi({
      "GET /api/auth/status": locked,
      "POST /api/auth/login": () => ({ status: 429, body: { detail: "x" } }),
    });
    renderApp();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Access token"), "t");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByText(/Too many failed attempts/),
    ).toBeInTheDocument();
  });
});
