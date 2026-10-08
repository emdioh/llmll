import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { AUTH_OFF, LEARNER, mockApi } from "../test/mockApi";
import DebugProvider from "./DebugProvider";
import {
  MAX_CALLS,
  extractPrompt,
  initialState,
  percentile,
  reducer,
} from "./debugStore";
import DebugView from "../views/DebugView";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((e: MessageEvent) => void)[]>();

  url: string;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  open() {
    this.readyState = 1;
    act(() => this.onopen?.());
  }

  fail() {
    this.readyState = 0;
    act(() => this.onerror?.());
  }

  emit(name: string, data: unknown) {
    act(() => {
      for (const fn of this.listeners.get(name) ?? [])
        fn(new MessageEvent(name, { data: JSON.stringify(data) }));
    });
  }
}

const T0 = Date.parse("2026-10-08T06:21:27.000Z");
const iso = (offsetMs: number) => new Date(T0 + offsetMs).toISOString();

const OPENAI_REQUEST = {
  model: "vendor/m",
  messages: [
    { role: "system", content: "You are a tutor." },
    { role: "user", content: "Explain the passato prossimo." },
  ],
};

function started(id: string, task = "explain", extra = {}) {
  return {
    id,
    ts: iso(0),
    task,
    provider: "openrouter",
    model: "vendor/m",
    prompt_version: "v1",
    request: OPENAI_REQUEST,
    request_chars: 40,
    ...extra,
  };
}

function finished(id: string, task = "explain", extra = {}) {
  return {
    ...started(id, task),
    ts: iso(1200),
    started_ts: iso(0),
    db_id: null,
    response: { text: "Ecco la spiegazione" },
    error: null,
    stop_reason: "stop",
    latency_ms: 1200,
    attempts: 2,
    http_statuses: [429, 200],
    retry_wait_ms: 300,
    ttfb_ms: 600,
    download_ms: 100,
    overhead_ms: 200,
    input_tokens: 100,
    output_tokens: 60,
    cache_read_tokens: null,
    cache_write_tokens: null,
    reasoning_tokens: 20,
    upstream_provider: "Fireworks",
    ...extra,
  };
}

const health = { status: "ok", version: "0", database: "ok", debug: true };

function setup(history: unknown[] = []) {
  mockApi({
    "GET /api/health": health,
    "GET /api/debug/llm/calls": history as never,
  });
  render(
    <MemoryRouter initialEntries={["/debug"]}>
      <DebugProvider>
        <DebugView />
      </DebugProvider>
    </MemoryRouter>,
  );
}

async function connect() {
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0]!;
  es.open();
  return es;
}

const rows = () => document.querySelectorAll(".dbg-row");

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  try {
    localStorage.clear();
  } catch {
    // ignore
  }
});

afterEach(() => {
  vi.useRealTimers();
});

describe("debug pane", () => {
  it("pairs call_started and call_finished by id", async () => {
    setup();
    const es = await connect();
    expect(screen.getByText("Live")).toBeInTheDocument();
    es.emit("call_started", started("a1"));
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("running")).toBeInTheDocument();
    es.emit("call_finished", finished("a1"));
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("ok")).toBeInTheDocument();
    expect(screen.queryByText("running")).not.toBeInTheDocument();
    const row = rows()[0]!;
    expect(row).toHaveTextContent("1.2 s");
    expect(row).toHaveTextContent("ttfb 600 ms");
    expect(row).toHaveTextContent("2 tries 429,200");
    expect(row).toHaveTextContent("retry wait 300 ms");
    expect(row).toHaveTextContent("100 / 60 / 20");
    expect(row).toHaveTextContent("100 tok/s");
    expect(row).toHaveTextContent("vendor/m via Fireworks");
    // a reconnect resends the started event: it must not revive the call
    es.emit("call_started", started("a1"));
    expect(rows()).toHaveLength(1);
    expect(screen.queryByText("running")).not.toBeInTheDocument();
  });

  it("shows an elapsed timer while a call runs", async () => {
    setup();
    const es = await connect();
    es.emit("call_started", started("r1", "explain", { ts: iso(0) }));
    const total = () => rows()[0]!.querySelector(".dbg-total")!.textContent;
    const first = total();
    expect(first).toMatch(/s$/);
    await waitFor(() => expect(total()).not.toBe(first));
  });

  it("renders an error row with the error text", async () => {
    setup();
    const es = await connect();
    es.emit(
      "call_finished",
      finished("e1", "grade_sentence", {
        response: null,
        error: "LLMUnavailable: boom",
        attempts: 3,
        http_statuses: [500, 500, null],
        ttfb_ms: null,
        download_ms: null,
        overhead_ms: null,
        output_tokens: null,
      }),
    );
    const row = rows()[0]!;
    expect(row).toHaveClass("st-error");
    expect(row).toHaveTextContent("3 tries 500,500,–");
    fireEvent.click(screen.getByRole("button", { name: /grade_sentence/ }));
    expect(screen.getByText("LLMUnavailable: boom")).toBeInTheDocument();
  });

  it("expands to the prompts and the pretty response, with copy buttons", async () => {
    setup();
    const es = await connect();
    es.emit("call_finished", finished("x1"));
    fireEvent.click(screen.getByRole("button", { name: /explain/ }));
    expect(screen.getByText("You are a tutor.")).toBeInTheDocument();
    expect(
      screen.getByText("Explain the passato prossimo."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/"text": "Ecco la spiegazione"/),
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Timing breakdown" })).toBeVisible();
    expect(screen.getByText("wait 300 ms")).toBeInTheDocument();

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "Copy system prompt" }));
    expect(writeText).toHaveBeenCalledWith("You are a tutor.");
    fireEvent.click(screen.getByRole("button", { name: "Copy response" }));
    expect(writeText).toHaveBeenLastCalledWith(
      JSON.stringify({ text: "Ecco la spiegazione" }, null, 2),
    );
    expect(await screen.findAllByText("Copied")).not.toHaveLength(0);
  });

  it("collapses long prompts behind show more", async () => {
    setup();
    const es = await connect();
    const long = "word ".repeat(400);
    es.emit(
      "call_finished",
      finished("l1", "explain", {
        request: {
          messages: [{ role: "user", content: long }],
        },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /explain/ }));
    const pre = document.querySelector(".dbg-pre")!;
    expect(pre).toHaveClass("clipped");
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(pre).not.toHaveClass("clipped");
  });

  it("buffers events while paused and applies them on resume", async () => {
    setup();
    const es = await connect();
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    es.emit("call_started", started("p1"));
    es.emit("call_finished", finished("p1"));
    expect(rows()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Resume (2)" }));
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("clears the list and ignores resent events of cleared calls", async () => {
    setup();
    const es = await connect();
    es.emit("call_finished", finished("c1"));
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(rows()).toHaveLength(0);
    es.emit("call_finished", finished("c1"));
    expect(rows()).toHaveLength(0);
    es.emit("call_finished", finished("c2"));
    expect(rows()).toHaveLength(1);
  });

  it("filters by task and shows p50/p90 per task", async () => {
    setup();
    const es = await connect();
    es.emit("call_finished", finished("f1", "explain"));
    es.emit("call_finished", finished("f2", "gloss", { latency_ms: 300 }));
    es.emit("call_finished", finished("f3", "gloss", { latency_ms: 500 }));
    expect(rows()).toHaveLength(3);
    const strip = screen.getByRole("list", { name: "Latency per task" });
    expect(strip).toHaveTextContent("gloss p50 400 ms · p90 480 ms · n=2");
    fireEvent.change(screen.getByLabelText("Filter by task"), {
      target: { value: "gloss" },
    });
    expect(rows()).toHaveLength(2);
    expect(strip).not.toHaveTextContent("explain");
  });

  it("loads history and dedupes it against live events by db_id", async () => {
    const row = (id: number, task: string) => ({
      ...finished("ignored", task),
      id,
      ts: iso(-60_000 * id),
    });
    setup([row(7, "explain"), row(5, "gloss")]);
    const es = await connect();
    await waitFor(() => expect(rows()).toHaveLength(2));
    // live finish of the call already stored as db row 7
    es.emit("call_finished", finished("live7", "explain", { db_id: 7 }));
    expect(rows()).toHaveLength(2);
    es.emit("call_finished", finished("live8", "explain", { db_id: 8 }));
    expect(rows()).toHaveLength(3);
  });

  it("shows the connection state", async () => {
    setup();
    const es = await connect();
    es.fail();
    expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
    es.open();
    expect(screen.getByText("Live")).toBeInTheDocument();
  });
});

describe("debug store", () => {
  it("caps the list at 200 calls", () => {
    let state = initialState;
    for (let i = 0; i < MAX_CALLS + 20; i++)
      state = reducer(state, {
        type: "event",
        message: {
          name: "call_started",
          data: started(`id${i}`, "explain", { ts: iso(i) }),
        },
      });
    expect(state.calls).toHaveLength(MAX_CALLS);
    expect(state.calls[0]!.uid).toBe(`id${MAX_CALLS + 19}`);
  });

  it("extracts prompts from the three provider shapes", () => {
    expect(extractPrompt(OPENAI_REQUEST)).toEqual({
      system: "You are a tutor.",
      user: "Explain the passato prossimo.",
    });
    expect(
      extractPrompt({
        system: [{ type: "text", text: "SYS" }],
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: "stable" },
              { type: "text", text: "variable" },
            ],
          },
        ],
      }),
    ).toEqual({ system: "SYS", user: "stable\n\nvariable" });
    expect(
      extractPrompt({
        config: { system_instruction: "SYS" },
        contents: ["stable", "variable"],
      }),
    ).toEqual({ system: "SYS", user: "stable\n\nvariable" });
    expect(extractPrompt({ lemma: "casa" })).toEqual({
      system: null,
      user: null,
    });
  });

  it("computes percentiles", () => {
    expect(percentile([100, 200, 300, 400], 0.5)).toBe(250);
    expect(percentile([10], 0.9)).toBe(10);
  });
});

describe("debug entry points", () => {
  function renderApp(debug: boolean) {
    mockApi({
      "GET /api/auth/status": AUTH_OFF,
      "GET /api/learner": LEARNER,
      "GET /api/grammar": [],
      "GET /api/health": { ...health, debug },
      "GET /api/debug/llm/calls": [],
    });
    return render(
      <MemoryRouter initialEntries={["/grammar"]}>
        <App />
      </MemoryRouter>,
    );
  }

  it("hides the nav entry and toggle when debug is off", async () => {
    renderApp(false);
    await screen.findByRole("heading", { name: "Grammar" });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("link", { name: "Debug" })).toBeNull();
    expect(screen.queryByRole("button", { name: /debug drawer/ })).toBeNull();
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("hides the nav entry and drawer on narrow screens even with debug on", async () => {
    localStorage.setItem("llmll.debugDrawer", "open");
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    try {
      renderApp(true);
      await screen.findByRole("heading", { name: "Grammar" });
      await new Promise((r) => setTimeout(r, 20));
      expect(screen.queryByRole("link", { name: "Debug" })).toBeNull();
      expect(
        screen.queryByRole("complementary", { name: "LLM debug" }),
      ).toBeNull();
      expect(screen.queryByRole("button", { name: /debug drawer/ })).toBeNull();
    } finally {
      window.matchMedia = original;
    }
  });

  it("shows the nav entry and a toggle that opens a persistent drawer", async () => {
    renderApp(true);
    expect(await screen.findByRole("link", { name: "Debug" })).toBeVisible();
    const es = await connect();
    fireEvent.click(
      screen.getByRole("button", { name: "Show LLM debug drawer" }),
    );
    expect(screen.getByText("No calls yet")).toBeInTheDocument();
    es.emit("call_finished", finished("d1", "simplify_text"));
    const bar = screen.getByRole("complementary", { name: "LLM debug" });
    expect(bar).toHaveTextContent("simplify_text");
    expect(bar).toHaveTextContent("1.2 s");
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    expect(rows()).toHaveLength(1);
    expect(localStorage.getItem("llmll.debugDrawer")).toBe("open");
    fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
    expect(rows()).toHaveLength(0);
    // the drawer is not shown on the Debug page itself
    fireEvent.click(screen.getByRole("link", { name: "Debug" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(rows()).toHaveLength(1);
  });

  it("restores the drawer state from localStorage", async () => {
    localStorage.setItem("llmll.debugDrawer", "bar");
    renderApp(true);
    expect(
      await screen.findByRole("complementary", { name: "LLM debug" }),
    ).toBeVisible();
  });
});
