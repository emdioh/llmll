import { useEffect, useState } from "react";

/** Current time, refreshed while `active` (for the elapsed timer of running calls). */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const tick = () => setNow(Date.now());
    tick();
    const id = setInterval(tick, 200);
    return () => clearInterval(id);
  }, [active]);
  return now;
}
