import type { MemoryEntry } from "./api/client";

export function nextDue(memory: MemoryEntry[]): Date | null {
  const dues = memory
    .map((m) => (m.due ? new Date(m.due) : null))
    .filter((d): d is Date => d !== null && !Number.isNaN(d.getTime()));
  if (dues.length === 0) return null;
  return new Date(Math.min(...dues.map((d) => d.getTime())));
}

export function formatDue(d: Date | null, now: Date = new Date()): string {
  if (!d) return "—";
  if (d.getTime() <= now.getTime()) return "due now";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function meanMastery(memory: MemoryEntry[]): number | null {
  if (memory.length === 0) return null;
  return memory.reduce((s, m) => s + m.mastery, 0) / memory.length;
}

export const STATUS_LABELS: Record<string, string> = {
  unseen: "Unseen",
  presumed_known: "Presumed known",
  candidate: "Candidate",
  introduced: "Learning",
  suspended: "Suspended",
};
