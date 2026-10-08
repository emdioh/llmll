/** "2026-10-05" (a local calendar day from the API) → a UTC-midnight Date. */
export function parseDay(s: string): Date {
  const [y = 1970, m = 1, d = 1] = s.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

export function fmtDay(s: string, long = false): string {
  return parseDay(s).toLocaleDateString(undefined, {
    timeZone: "UTC",
    weekday: long ? "short" : undefined,
    day: "numeric",
    month: "short",
  });
}

export function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}

export function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`;
}

export function minutesLabel(m: number | null | undefined): string {
  if (m === null || m === undefined) return "—";
  return m < 1 ? "<1 min" : `${Math.round(m)} min`;
}

export const STATE_LABELS = {
  new: "Not yet",
  learning: "Learning",
  young: "Young",
  mature: "Mature",
  presumed_known: "Presumed known",
} as const;

export const TAG_LABELS = (tag: string) => tag.replace(/[_-]+/g, " ");
