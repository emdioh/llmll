import { createContext, useContext } from "react";
import type { DebugCall } from "./debugStore";

export type Connection = "connecting" | "open" | "reconnecting" | "closed";
export type DrawerState = "off" | "bar" | "open";

export interface DebugContextValue {
  /** `/api/health` reported `debug: true`. */
  enabled: boolean;
  calls: DebugCall[];
  connection: Connection;
  paused: boolean;
  /** Events held back while paused. */
  pendingCount: number;
  setPaused: (paused: boolean) => void;
  clear: () => void;
  drawer: DrawerState;
  setDrawer: (state: DrawerState) => void;
}

export const DebugContext = createContext<DebugContextValue>({
  enabled: false,
  calls: [],
  connection: "closed",
  paused: false,
  pendingCount: 0,
  setPaused: () => {},
  clear: () => {},
  drawer: "off",
  setDrawer: () => {},
});

export function useDebug(): DebugContextValue {
  return useContext(DebugContext);
}
