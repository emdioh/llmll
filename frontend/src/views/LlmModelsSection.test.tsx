import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import LlmModelsSection from "./LlmModelsSection";

const task = (name: string, extra: object = {}) => ({
  task: name,
  provider: "anthropic",
  model: "claude-x",
  structured_output: "native",
  source: "config",
  config_provider: "anthropic",
  config_model: "claude-x",
  override: null,
  ...extra,
});
const settings = (over: object = {}) => ({
  fake: false,
  live_switch: true,
  override_error: null,
  available_providers: ["anthropic", "openrouter"],
  tasks: [
    task("grade_sentence"),
    task("explain"),
    task("generate_exercise"),
    task("simplify_text"),
    task("gloss"),
  ],
  ...over,
});

describe("LlmModelsSection", () => {
  it("renders the rows and saves a changed model", async () => {
    let put: unknown;
    mockApi({
      "GET /api/settings/llm": settings(),
      "PUT /api/settings/llm": ({ body }) => {
        put = body;
        return { body: settings() };
      },
    });
    render(<LlmModelsSection />);
    const model = await screen.findByLabelText("Grading model");
    expect(model).toHaveAttribute("placeholder", "claude-x");
    expect(screen.getByText("Other tasks")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(model, "claude-y");
    await user.click(screen.getByRole("button", { name: "Save models" }));
    expect(await screen.findByText("LLM models saved.")).toBeInTheDocument();
    expect(put).toEqual({
      tasks: { grade_sentence: { provider: "anthropic", model: "claude-y" } },
    });
  });

  it("changing the provider clears the model and sends the output mode", async () => {
    let put: unknown;
    mockApi({
      "GET /api/settings/llm": settings(),
      "PUT /api/settings/llm": ({ body }) => {
        put = body;
        return { body: settings() };
      },
    });
    render(<LlmModelsSection />);
    const user = userEvent.setup();
    await user.selectOptions(
      await screen.findByLabelText("Explanations provider"),
      "openrouter",
    );
    await user.click(screen.getByRole("button", { name: "Save models" }));
    expect(
      await screen.findByText("Enter a model for Explanations."),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("Explanations model"), "v/m");
    await user.selectOptions(
      screen.getByLabelText("Explanations output"),
      "json",
    );
    await user.click(screen.getByRole("button", { name: "Save models" }));
    await screen.findByText("LLM models saved.");
    expect(put).toEqual({
      tasks: {
        explain: {
          provider: "openrouter",
          model: "v/m",
          structured_output: "json",
        },
      },
    });
  });

  it("reset sends null and an override shows the .env route", async () => {
    let put: unknown;
    const overridden = settings({
      tasks: [
        task("grade_sentence", {
          provider: "openrouter",
          model: "v/m",
          source: "override",
          override: {
            provider: "openrouter",
            model: "v/m",
            structured_output: null,
          },
        }),
        task("explain"),
      ],
    });
    mockApi({
      "GET /api/settings/llm": overridden,
      "PUT /api/settings/llm": ({ body }) => {
        put = body;
        return { body: settings() };
      },
    });
    render(<LlmModelsSection />);
    expect(
      await screen.findByText("from .env: anthropic/claude-x"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Grading model")).toHaveValue("v/m");
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Reset Grading" }));
    await waitFor(() =>
      expect(screen.queryByText(/from \.env/)).not.toBeInTheDocument(),
    );
    expect(put).toEqual({ tasks: { grade_sentence: null } });
    expect(screen.queryByText(/from \.env/)).not.toBeInTheDocument();
  });

  it("shows the server's 422 message", async () => {
    mockApi({
      "GET /api/settings/llm": settings(),
      "PUT /api/settings/llm": () => ({
        status: 422,
        body: { detail: "task 'gloss': bad model" },
      }),
    });
    render(<LlmModelsSection />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Grading model"), "zzz");
    await user.click(screen.getByRole("button", { name: "Save models" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("bad model");
  });

  it("shows a notice instead of inputs for the simulated LLM", async () => {
    mockApi({ "GET /api/settings/llm": settings({ fake: true, tasks: [] }) });
    render(<LlmModelsSection />);
    expect(
      await screen.findByText(/simulated LLM is in use/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows the override error and the restart note", async () => {
    mockApi({
      "GET /api/settings/llm": settings({
        override_error: "OPENROUTER_API_KEY is not set",
        live_switch: false,
      }),
    });
    render(<LlmModelsSection />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "OPENROUTER_API_KEY is not set",
    );
    expect(screen.getByText(/after restarting the server/)).toBeInTheDocument();
  });
});
