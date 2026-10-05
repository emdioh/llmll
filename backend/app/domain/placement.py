"""Initial assessment: sampling and level estimation (design: docs/design/M4.md §3). Pure."""

import random
from collections.abc import Sequence
from dataclasses import dataclass

from app.curriculum.schema import CEFR_LEVELS
from app.domain.config import PlacementConfig

TERCILES = 3
SCORES = {"correct": 1.0, "assisted": 0.5, "error": 0.0}


@dataclass(frozen=True)
class PlacementLemma:
    item_id: str
    level: str
    frequency_zipf: float | None = None


@dataclass(frozen=True)
class PlacementGrammar:
    item_id: str
    level: str
    order: int  # curriculum order


@dataclass(frozen=True)
class PlacementResult:
    level: str  # CEFR level of the item that was tested
    outcome: str  # correct | assisted | error


def _rank(level: str) -> int:
    return CEFR_LEVELS.index(level)


def _band_sample(
    pool: list[PlacementLemma], count: int, rng: random.Random
) -> list[PlacementLemma]:
    """`count` lemmas of one band, spread over the frequency terciles (high, mid, low)."""
    ordered = sorted(pool, key=lambda p: (-(p.frequency_zipf or 0.0), p.item_id))
    size = len(ordered)
    terciles = [ordered[size * i // TERCILES : size * (i + 1) // TERCILES] for i in range(TERCILES)]
    for tercile in terciles:
        rng.shuffle(tercile)
    chosen: list[PlacementLemma] = []
    while len(chosen) < count and any(terciles):
        for tercile in terciles:
            if tercile and len(chosen) < count:
                chosen.append(tercile.pop())
    return chosen


def sample_placement_lemmas(
    items: Sequence[PlacementLemma],
    declared_level: str,
    seed: str | int,
    cfg: PlacementConfig | None = None,
) -> list[str]:
    """Lemma ids for the vocabulary part, easiest band first and most frequent first.

    `vocab_per_band` lemmas for each of the levels declared-1, declared and declared+1,
    stratified by `frequency_zipf` terciles. A band with too few lemmas (or no band at all, e.g.
    below A1) hands its share to the other bands, the declared band first. Deterministic by seed.
    """
    cfg = cfg or PlacementConfig()
    rank = _rank(declared_level)
    # Bands in priority order: declared, above, below.
    band_ranks = [r for r in (rank, rank + 1, rank - 1) if 0 <= r < len(CEFR_LEVELS)]
    pools = {r: [p for p in items if _rank(p.level) == r and p.item_id] for r in band_ranks}
    pools = {r: pool for r, pool in pools.items() if pool}
    bands = [r for r in band_ranks if r in pools]
    quota = {r: min(cfg.vocab_per_band, len(pools[r])) for r in bands}
    missing = cfg.vocab_per_band * len(band_ranks) - sum(quota.values())
    while missing > 0:
        grown = False
        for r in bands:
            if missing > 0 and quota[r] < len(pools[r]):
                quota[r] += 1
                missing -= 1
                grown = True
        if not grown:
            break
    chosen: list[PlacementLemma] = []
    for r in sorted(bands):
        rng = random.Random(f"{seed}|{CEFR_LEVELS[r]}")
        chosen.extend(_band_sample(pools[r], quota[r], rng))
    chosen.sort(key=lambda p: (_rank(p.level), -(p.frequency_zipf or 0.0), p.item_id))
    return [p.item_id for p in chosen]


def select_placement_grammar(
    items: Sequence[PlacementGrammar],
    declared_level: str,
    cfg: PlacementConfig | None = None,
) -> list[str]:
    """Grammar points for the production part (`grammar_count` at most).

    The latest word-order stages up to the declared level come first (at most
    `grammar_count - min_other_grammar` of them), then points at the declared level and one
    above it, alternating. Result ordered by level and curriculum order.
    """
    cfg = cfg or PlacementConfig()
    rank = _rank(declared_level)
    by_id = {g.item_id: g for g in items}
    stage_ids = [i for i, _ in cfg.word_order_stages]
    stages = [i for i, lvl in cfg.word_order_stages if _rank(lvl) <= rank and i in by_id]
    max_stages = max(cfg.grammar_count - cfg.min_other_grammar, 0)
    chosen = stages[len(stages) - max_stages :] if len(stages) > max_stages else stages
    others = sorted(
        (g for g in items if g.item_id not in stage_ids and g.item_id not in chosen),
        key=lambda g: (g.order, g.item_id),
    )
    at = [g for g in others if _rank(g.level) == rank]
    above = [g for g in others if _rank(g.level) == rank + 1]
    below = [g for g in others if _rank(g.level) == rank - 1]
    queues = [at, above, at, below]
    # Alternate declared / above / declared / below, draining each list in turn.
    picked: list[str] = []
    while len(chosen) + len(picked) < cfg.grammar_count and any(queues):
        for q in queues:
            if q and len(chosen) + len(picked) < cfg.grammar_count:
                picked.append(q.pop(0).item_id)
    result = [*chosen, *picked]
    result.sort(key=lambda i: (_rank(by_id[i].level), by_id[i].order, i))
    return result


def _rate(results: Sequence[PlacementResult], rank: int, minimum: int) -> float | None:
    scores = [SCORES[r.outcome] for r in results if _rank(r.level) == rank]
    if len(scores) < minimum:
        return None
    return sum(scores) / len(scores)


def estimate_level(
    results: Sequence[PlacementResult],
    declared_level: str,
    cfg: PlacementConfig | None = None,
) -> str:
    """The level the evidence supports: raise by one band when the learner does well one band
    above the declared level, lower by one when they do poorly at the declared level.
    Bands with fewer than `min_band_results` results are not evidence."""
    cfg = cfg or PlacementConfig()
    rank = _rank(declared_level)
    above = _rate(results, rank + 1, cfg.min_band_results)
    if above is not None and above >= cfg.raise_threshold and rank + 1 < len(CEFR_LEVELS):
        return CEFR_LEVELS[rank + 1]
    at = _rate(results, rank, cfg.min_band_results)
    if at is not None and at < cfg.lower_threshold and rank > 0:
        return CEFR_LEVELS[rank - 1]
    return declared_level
