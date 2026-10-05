import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import App from "./App";
import { AUTH_OFF, LEARNER, mockApi } from "./test/mockApi";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App", () => {
  it("renders the shell and navigates between views", async () => {
    mockApi({
      "GET /api/auth/status": AUTH_OFF,
      "GET /api/learner": LEARNER,
      "GET /api/grammar": [],
    });
    renderAt("/");
    expect(screen.getByText("LLMLL")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Session" }),
    ).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("link", { name: "Reading" }));
    expect(
      screen.getByRole("heading", { name: "Reading" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "Grammar" }));
    expect(
      screen.getByRole("heading", { name: "Grammar" }),
    ).toBeInTheDocument();
  });

  it("shows onboarding on 404 and submits the setup form", async () => {
    let created: unknown = null;
    mockApi({
      "GET /api/auth/status": AUTH_OFF,
      "GET /api/learner": () => ({ status: 404, body: { detail: "none" } }),
      "GET /api/queue": {
        budget: { lemmas_left: 0, grammar_left: 0, backlog: 0 },
        next: [],
      },
      "POST /api/learner": ({ body }) => {
        created = body;
        return { status: 201, body: { ...LEARNER, level: "B1" } };
      },
    });
    renderAt("/");
    expect(
      await screen.findByRole("heading", { name: "Welcome" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Italian")).toBeChecked();
    expect(screen.getByLabelText("English")).toBeChecked();
    expect(screen.getByLabelText("French")).not.toBeChecked();

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Your German level"), "B1");
    await user.click(screen.getByRole("button", { name: "Start learning" }));

    // The placement test is offered after setup and can be skipped.
    expect(
      await screen.findByRole("heading", { name: "Placement test" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Quick placement test/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Skip" }));
    expect(
      await screen.findByRole("heading", { name: "Session" }),
    ).toBeInTheDocument();
    expect(created).toEqual({
      level: "B1",
      known_languages: ["it", "en"],
      explanation_language: "it",
    });
  });
});

describe("App fake-LLM banner", () => {
  const text =
    "Running without an LLM API key: exercises and grading are simulated.";

  it("shows the banner when the backend uses the fake LLM and lets it be dismissed", async () => {
    sessionStorage.clear();
    mockApi({
      "GET /api/auth/status": AUTH_OFF,
      "GET /api/learner": LEARNER,
      "GET /api/health": {
        status: "ok",
        version: "1",
        database: "ok",
        llm: "fake",
      },
    });
    renderAt("/");
    expect(await screen.findByText(text)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(text)).not.toBeInTheDocument();
  });

  it("shows no banner with a real LLM", async () => {
    sessionStorage.clear();
    mockApi({
      "GET /api/auth/status": AUTH_OFF,
      "GET /api/learner": LEARNER,
      "GET /api/health": {
        status: "ok",
        version: "1",
        database: "ok",
        llm: "anthropic",
      },
    });
    renderAt("/");
    await screen.findByRole("heading", { name: "Session" });
    expect(screen.queryByText(text)).not.toBeInTheDocument();
  });
});
