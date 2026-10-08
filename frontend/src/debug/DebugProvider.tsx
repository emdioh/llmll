import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useState,
  type ReactNode,
} from "react";
import { getDebugCalls, getHealth } from "../api/client";
import {
  DebugContext,
  type Connection,
  type DrawerState,
} from "./debugContext";
import { initialState, reducer, type EventName } from "./debugStore";

const EVENTS_URL = "/api/debug/llm/events";
const DRAWER_KEY = "llmll.debugDrawer";

function readDrawer(): DrawerState {
  try {
    const v = localStorage.getItem(DRAWER_KEY);
    if (v === "bar" || v === "open") return v;
  } catch {
    // storage unavailable: start closed
  }
  return "off";
}

/**
 * Holds the single `EventSource` connection and the call list shared by the Debug page and the
 * drawer. Nothing is opened unless `/api/health` reports `debug: true`.
 */
export default function DebugProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState(false);
  const [state, dispatch] = useReducer(reducer, initialState);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [drawer, setDrawerState] = useState<DrawerState>(readDrawer);

  useEffect(() => {
    let cancelled = false;
    getHealth().then(
      (h) => {
        if (!cancelled) setEnabled(h.debug === true);
      },
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    getDebugCalls(50).then(
      (rows) => {
        if (!cancelled) dispatch({ type: "history", rows });
      },
      () => {},
    );
    if (typeof EventSource === "undefined") {
      return () => {
        cancelled = true;
      };
    }
    const source = new EventSource(EVENTS_URL);
    source.onopen = () => setConnection("open");
    source.onerror = () =>
      setConnection(source.readyState === 2 ? "closed" : "reconnecting");
    const listen = (name: EventName) =>
      source.addEventListener(name, (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent<string>).data);
          if (data && typeof data === "object")
            dispatch({ type: "event", message: { name, data } });
        } catch {
          // ignore a malformed frame
        }
      });
    listen("call_started");
    listen("call_finished");
    return () => {
      cancelled = true;
      source.close();
    };
  }, [enabled]);

  const setPaused = useCallback(
    (paused: boolean) => dispatch({ type: "pause", paused }),
    [],
  );
  const clear = useCallback(() => dispatch({ type: "clear" }), []);
  const setDrawer = useCallback((next: DrawerState) => {
    setDrawerState(next);
    try {
      localStorage.setItem(DRAWER_KEY, next);
    } catch {
      // not remembered
    }
  }, []);

  const value = useMemo(
    () => ({
      enabled,
      calls: state.calls,
      connection,
      paused: state.paused,
      pendingCount: state.pending.length,
      setPaused,
      clear,
      drawer: enabled ? drawer : ("off" as const),
      setDrawer,
    }),
    [enabled, state, connection, drawer, setPaused, clear, setDrawer],
  );
  return (
    <DebugContext.Provider value={value}>{children}</DebugContext.Provider>
  );
}
