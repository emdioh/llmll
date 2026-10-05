import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App", () => {
  it("renders the shell and navigates between views", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    renderAt("/");
    expect(screen.getByText("LLMLL")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Session" }),
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
});
