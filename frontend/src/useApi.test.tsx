import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useApi } from "./useApi";

function Probe({
  id,
  load,
}: {
  id: string;
  load: (id: string) => Promise<string>;
}) {
  // An inline loader: a new function on every render.
  const state = useApi(`probe-${id}`, () => load(id));
  return <p>{state.status === "ok" ? state.data : state.status}</p>;
}

describe("useApi", () => {
  it("does not refetch when only the loader's identity changes", async () => {
    const load = vi.fn((id: string) => Promise.resolve(`data ${id}`));
    const { rerender } = render(<Probe id="a" load={load} />);
    expect(await screen.findByText("data a")).toBeInTheDocument();
    rerender(<Probe id="a" load={load} />);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("refetches with the latest loader when the key changes", async () => {
    const load = vi.fn((id: string) => Promise.resolve(`data ${id}`));
    const { rerender } = render(<Probe id="a" load={load} />);
    await screen.findByText("data a");
    rerender(<Probe id="b" load={load} />);
    expect(await screen.findByText("data b")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(2);
  });
});
