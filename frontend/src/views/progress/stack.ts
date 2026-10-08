import type { LevelProgress } from "../../api/client";
import type { StackRow } from "./charts";

const LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"];

/** Sums the level-progress rows of the given kinds into one stacked-bar row per CEFR level. */
export function levelRows(
  levels: LevelProgress[],
  kinds: string[],
): StackRow[] {
  return LEVELS.map((label) => {
    const counts = {
      mature: 0,
      young: 0,
      learning: 0,
      presumed_known: 0,
      new: 0,
    };
    for (const l of levels) {
      if (l.level !== label || !kinds.includes(l.kind)) continue;
      counts.mature += l.mature;
      counts.young += l.young;
      counts.learning += l.learning;
      counts.presumed_known += l.presumed_known;
      counts.new += l.new;
    }
    return { label, counts };
  });
}
