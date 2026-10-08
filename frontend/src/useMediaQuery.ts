import { useEffect, useState } from "react";

/** Live result of a CSS media query. Without `matchMedia` (tests, old browsers) returns
 * `fallback`. */
export function useMediaQuery(query: string, fallback = true): boolean {
  const get = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : fallback;
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}

/** Screens wide enough for developer tools such as the LLM debug pane (same breakpoint as
 * the side navigation). */
export const WIDE_SCREEN = "(min-width: 768px)";
