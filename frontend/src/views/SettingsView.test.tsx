import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SettingsView from "./SettingsView";

describe("SettingsView", () => {
  it("shows backend ok state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok", version: "0.1.0", database: "ok" }),
      }),
    );
    render(<SettingsView />);
    expect(
      await screen.findByText("Backend: ok (v0.1.0), database: ok"),
    ).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/health");
  });

  it("shows error state when fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down")),
    );
    render(<SettingsView />);
    expect(await screen.findByText(/Backend: unreachable/)).toBeInTheDocument();
  });
});
