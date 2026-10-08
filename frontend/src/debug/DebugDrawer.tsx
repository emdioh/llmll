import { useLocation } from "react-router";
import DebugPane from "./DebugPane";
import { useDebug } from "./debugContext";
import { totalLabel } from "./debugStore";
import { useMediaQuery, WIDE_SCREEN } from "../useMediaQuery";
import { useNow } from "./useNow";

function LastCall() {
  const { calls } = useDebug();
  const last = calls[0];
  const now = useNow(last?.state === "running");
  if (!last) return <span className="muted">No calls yet</span>;
  return (
    <span className="dbg-last">
      <strong>{last.task}</strong>
      <span className={`badge st-${last.state}`}>{last.state}</span>
      <span>{totalLabel(last, now)}</span>
    </span>
  );
}

/** Floating toggle plus bottom drawer; wide screens only, hidden on the Debug page itself. */
export default function DebugDrawer() {
  const { enabled, drawer, setDrawer } = useDebug();
  const { pathname } = useLocation();
  const wide = useMediaQuery(WIDE_SCREEN);
  if (!enabled || !wide || pathname === "/debug") return null;
  if (drawer === "off")
    return (
      <button
        type="button"
        className="dbg-toggle"
        aria-label="Show LLM debug drawer"
        onClick={() => setDrawer("bar")}
      >
        Debug
      </button>
    );
  return (
    <aside className={`dbg-drawer drawer-${drawer}`} aria-label="LLM debug">
      <div className="dbg-bar-row">
        <LastCall />
        <span className="dbg-bar-actions">
          <button
            type="button"
            className="btn small"
            aria-expanded={drawer === "open"}
            onClick={() => setDrawer(drawer === "open" ? "bar" : "open")}
          >
            {drawer === "open" ? "Collapse" : "Expand"}
          </button>
          <button
            type="button"
            className="btn small"
            aria-label="Hide LLM debug drawer"
            onClick={() => setDrawer("off")}
          >
            Hide
          </button>
        </span>
      </div>
      {drawer === "open" && <DebugPane variant="drawer" />}
    </aside>
  );
}

/** Scroll room at the end of the page so the open drawer never hides the last inputs. */
export function DebugSpacer() {
  const { enabled, drawer } = useDebug();
  const { pathname } = useLocation();
  const wide = useMediaQuery(WIDE_SCREEN);
  if (!enabled || !wide || pathname === "/debug" || drawer === "off")
    return null;
  return <div className={`dbg-spacer drawer-${drawer}`} aria-hidden="true" />;
}
