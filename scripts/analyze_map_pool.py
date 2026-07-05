"""Statistical audit of duel_map_pool — diagnoses parser/classification bias.

Reads from the live database (or a CSV dump path passed as argv[1]) and
prints distributions + correlations relevant to the map_type classifier
debate (see plan unified-giggling-tiger and parser bias investigation).

Hypotheses we want to confirm or refute:
  H1. osu! API blending (api_aim_diff / api_speed_diff) dominates intrinsics,
      flattening differences between stream and aim maps.
  H2. cons_mult (1.1..1.5 by SR) gives consistency an unfair boost on long
      jump maps, mislabeling them as 'cons' instead of 'aim'.
  H3. argmax classification has no margin — many maps are "mixed" with
      top-2 axes within 0.5★ of each other and get misclassified by noise.

What this script prints
-----------------------
1. Coverage: how many maps have stars populated, how many are NULL.
2. Distribution per axis: median / p25 / p75 / max / std.
3. map_type distribution: % of maps per type, ideal would be ~25% each.
4. Argmax margin distribution: % of maps where top1 - top2 < 0.5, < 1.0.
5. Correlation matrix: aim_stars vs api_aim_diff, speed_stars vs api_speed_diff,
   cons_stars vs length, cons_stars vs star_rating.
6. Per-type SR histogram: maps in each type by SR bucket (3-4 / 4-5 / ...).
7. Suspicious-classification examples: maps where the runner-up axis is
   within 0.3★ — print 10 worst cases sorted by ambiguity.

Usage
-----
    # Use the bot's configured DATABASE_URL:
    python3 -m scripts.analyze_map_pool

    # Or feed a CSV dump (export from VPS):
    #   sqlite3 bot.db -header -csv 'SELECT * FROM duel_map_pool' > pool.csv
    python3 -m scripts.analyze_map_pool pool.csv
"""

from __future__ import annotations

import asyncio
import csv
import math
import statistics
import sys
from dataclasses import dataclass
from typing import Optional


# ── Row model ───────────────────────────────────────────────────────────────

@dataclass
class MapRow:
    beatmap_id: int
    title: str
    star_rating: float
    length: int
    bpm: float
    aim_stars: Optional[float]
    speed_stars: Optional[float]
    acc_stars: Optional[float]
    cons_stars: Optional[float]
    map_type: Optional[str]
    api_aim_diff: Optional[float]
    api_speed_diff: Optional[float]

    @classmethod
    def from_csv(cls, row: dict) -> "MapRow":
        def f(k: str) -> Optional[float]:
            v = row.get(k)
            if v in (None, "", "NULL"):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def i(k: str) -> int:
            v = row.get(k)
            try:
                return int(float(v)) if v not in (None, "", "NULL") else 0
            except (TypeError, ValueError):
                return 0

        return cls(
            beatmap_id=i("beatmap_id"),
            title=str(row.get("title") or "")[:80],
            star_rating=f("star_rating") or 0.0,
            length=i("length"),
            bpm=f("bpm") or 0.0,
            aim_stars=f("aim_stars"),
            speed_stars=f("speed_stars"),
            acc_stars=f("acc_stars"),
            cons_stars=f("cons_stars"),
            map_type=row.get("map_type"),
            api_aim_diff=f("api_aim_diff"),
            api_speed_diff=f("api_speed_diff"),
        )

    @classmethod
    def from_orm(cls, row) -> "MapRow":
        return cls(
            beatmap_id=row.beatmap_id,
            title=(row.title or "")[:80],
            star_rating=row.star_rating or 0.0,
            length=row.length or 0,
            bpm=row.bpm or 0.0,
            aim_stars=row.aim_stars,
            speed_stars=row.speed_stars,
            acc_stars=row.acc_stars,
            cons_stars=row.cons_stars,
            map_type=row.map_type,
            api_aim_diff=row.api_aim_diff,
            api_speed_diff=row.api_speed_diff,
        )


# ── Loaders ─────────────────────────────────────────────────────────────────

def load_csv(path: str) -> list[MapRow]:
    rows: list[MapRow] = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append(MapRow.from_csv(r))
    return rows


async def load_db() -> list[MapRow]:
    """Load from the configured DATABASE_URL via SQLAlchemy."""
    from sqlalchemy import select
    from db.database import get_db_session
    from db.models.duel_map_pool import DuelMapPool

    async with get_db_session() as session:
        result = await session.execute(select(DuelMapPool))
        return [MapRow.from_orm(r) for r in result.scalars().all()]


# ── Statistics helpers ──────────────────────────────────────────────────────

def _quantiles(xs: list[float]) -> dict:
    """Return median / p25 / p75 / max / std for a list of floats."""
    if not xs:
        return {"n": 0}
    xs_sorted = sorted(xs)
    n = len(xs)
    return {
        "n": n,
        "min": xs_sorted[0],
        "p25": xs_sorted[n // 4],
        "median": xs_sorted[n // 2],
        "p75": xs_sorted[(3 * n) // 4],
        "max": xs_sorted[-1],
        "std": statistics.stdev(xs) if n > 1 else 0.0,
        "mean": statistics.mean(xs),
    }


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _sr_bucket(sr: float) -> str:
    return f"{int(sr)}-{int(sr)+1}★"


# ── Reports ─────────────────────────────────────────────────────────────────

def _table(rows: list[tuple], headers: list[str]) -> str:
    """Tiny ASCII table renderer."""
    cols = list(zip(*([headers] + rows)))
    widths = [max(len(str(c)) for c in col) for col in cols]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    out = [sep, "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |", sep]
    for r in rows:
        out.append("| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)) + " |")
    out.append(sep)
    return "\n".join(out)


def report_coverage(rows: list[MapRow]) -> None:
    print("\n══════ COVERAGE ══════")
    total = len(rows)
    with_stars = sum(1 for r in rows if all(
        v is not None for v in (r.aim_stars, r.speed_stars, r.acc_stars, r.cons_stars)
    ))
    with_api = sum(1 for r in rows if r.api_aim_diff is not None)
    with_type = sum(1 for r in rows if r.map_type)
    print(f"Total maps:        {total}")
    print(f"Stars populated:   {with_stars} ({100*with_stars/max(total,1):.1f}%)")
    print(f"API attrs present: {with_api} ({100*with_api/max(total,1):.1f}%)")
    print(f"map_type tagged:   {with_type} ({100*with_type/max(total,1):.1f}%)")


def report_distributions(rows: list[MapRow]) -> None:
    print("\n══════ PER-AXIS STARS DISTRIBUTION ══════")
    print("Each axis should ideally cover [0..10] with median near SR-median.\n")
    axes = ("aim_stars", "speed_stars", "acc_stars", "cons_stars", "star_rating")
    table = []
    for ax in axes:
        vs = [getattr(r, ax) for r in rows if getattr(r, ax) is not None]
        q = _quantiles(vs)
        if q.get("n", 0) == 0:
            continue
        table.append((
            ax,
            q["n"],
            f"{q['mean']:.2f}",
            f"{q['median']:.2f}",
            f"{q['p25']:.2f}",
            f"{q['p75']:.2f}",
            f"{q['max']:.2f}",
            f"{q['std']:.2f}",
        ))
    print(_table(table, ["axis", "n", "mean", "p50", "p25", "p75", "max", "std"]))


def report_map_type(rows: list[MapRow]) -> None:
    print("\n══════ MAP_TYPE DISTRIBUTION ══════")
    print("Ideal: ~25% per type. Skew suggests the argmax classifier is biased.\n")
    counts: dict[str, int] = {}
    for r in rows:
        if r.map_type:
            counts[r.map_type] = counts.get(r.map_type, 0) + 1
    total = sum(counts.values()) or 1
    table = []
    for typ in ("aim", "speed", "acc", "cons"):
        c = counts.get(typ, 0)
        table.append((typ, c, f"{100*c/total:.1f}%"))
    print(_table(table, ["type", "count", "share"]))


def report_argmax_margin(rows: list[MapRow]) -> None:
    print("\n══════ ARGMAX MARGIN (TOP1 − TOP2) ══════")
    print("Margins below 0.5★ are ambiguous — classifier is essentially noisy.\n")
    margins: list[float] = []
    for r in rows:
        vs = [r.aim_stars, r.speed_stars, r.acc_stars, r.cons_stars]
        if any(v is None for v in vs):
            continue
        sv = sorted(vs, reverse=True)
        margins.append(sv[0] - sv[1])
    if not margins:
        print("(no maps with full per-axis stars)")
        return
    q = _quantiles(margins)
    print(f"n={q['n']}  median={q['median']:.2f}  p25={q['p25']:.2f}  "
          f"p75={q['p75']:.2f}  max={q['max']:.2f}")
    thresholds = [0.1, 0.3, 0.5, 1.0, 2.0]
    print("\nFraction of maps with margin BELOW threshold:")
    for t in thresholds:
        below = sum(1 for m in margins if m < t)
        print(f"  margin < {t:>3}★  → {below:5d} maps  ({100*below/len(margins):5.1f}%)")


def report_correlations(rows: list[MapRow]) -> None:
    print("\n══════ CORRELATIONS ══════")
    print("High |r| between our stars and OSU api → blending dominates intrinsics.\n")

    def pull(getter) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for r in rows:
            a, b = getter(r)
            if a is None or b is None:
                continue
            out.append((float(a), float(b)))
        return out

    pairs = [
        ("aim_stars vs api_aim_diff",     lambda r: (r.aim_stars, r.api_aim_diff)),
        ("speed_stars vs api_speed_diff", lambda r: (r.speed_stars, r.api_speed_diff)),
        ("aim_stars vs star_rating",      lambda r: (r.aim_stars, r.star_rating)),
        ("speed_stars vs star_rating",    lambda r: (r.speed_stars, r.star_rating)),
        ("cons_stars vs length",          lambda r: (r.cons_stars, r.length)),
        ("cons_stars vs star_rating",     lambda r: (r.cons_stars, r.star_rating)),
        ("acc_stars vs star_rating",      lambda r: (r.acc_stars, r.star_rating)),
        ("aim_stars vs speed_stars",      lambda r: (r.aim_stars, r.speed_stars)),
        ("aim_stars vs cons_stars",       lambda r: (r.aim_stars, r.cons_stars)),
    ]
    table = []
    for name, getter in pairs:
        data = pull(getter)
        if len(data) < 3:
            table.append((name, "—", "—"))
            continue
        r = _pearson([a for a, _ in data], [b for _, b in data])
        marker = ""
        if r is not None:
            if abs(r) >= 0.85:
                marker = "  ⚠️ VERY HIGH"
            elif abs(r) >= 0.7:
                marker = "  ⚠ high"
        table.append((name, len(data), f"{r:+.3f}{marker}" if r is not None else "—"))
    print(_table(table, ["pair", "n", "Pearson r"]))


def report_type_sr_histogram(rows: list[MapRow]) -> None:
    print("\n══════ MAP_TYPE × SR BUCKET ══════")
    print("If 'cons' dominates at high SR, that's H2 (cons_mult bias).\n")
    buckets: dict[tuple[str, str], int] = {}
    sr_buckets_seen: set[str] = set()
    for r in rows:
        if not r.map_type:
            continue
        sb = _sr_bucket(r.star_rating)
        sr_buckets_seen.add(sb)
        buckets[(sb, r.map_type)] = buckets.get((sb, r.map_type), 0) + 1
    sr_sorted = sorted(sr_buckets_seen, key=lambda s: int(s.split("-")[0]))
    types = ("aim", "speed", "acc", "cons")
    table = []
    for sb in sr_sorted:
        row_total = sum(buckets.get((sb, t), 0) for t in types) or 1
        cells = []
        for t in types:
            c = buckets.get((sb, t), 0)
            cells.append(f"{c} ({100*c/row_total:.0f}%)")
        table.append((sb, row_total, *cells))
    print(_table(table, ["SR bucket", "n", "aim", "speed", "acc", "cons"]))


def report_duel_and_length(rows: list[MapRow]) -> None:
    """DUEL_map composite distribution + length distribution.

    DUEL_map = mean(aim, speed, acc, cons) since pool rows lack per-axis weights.
    This matches services/bounty/tier_rules.compute_duel_map's fallback path
    (axes equal-weighted at 0.25) for rows where w_* columns are NULL.
    """
    print("\n══════ DUEL_map COMPOSITE & LENGTH ══════")
    print("DUEL_map = 0.25·(aim+speed+acc+cons). Used by tier_rules.TIER_DUEL_RANGES.\n")

    duel: list[float] = []
    for r in rows:
        vs = (r.aim_stars, r.speed_stars, r.acc_stars, r.cons_stars)
        if any(v is None for v in vs):
            continue
        duel.append(sum(vs) / 4.0)  # type: ignore[arg-type]
    q = _quantiles(duel)
    if q.get("n", 0) == 0:
        print("(no maps with full axis data)")
        return
    print(f"DUEL_map: n={q['n']}  mean={q['mean']:.2f}  p25={q['p25']:.2f}  "
          f"p50={q['median']:.2f}  p75={q['p75']:.2f}  max={q['max']:.2f}")

    # Buckets for tier calibration — show how many maps fall in each candidate
    # range so we can pick percentile-based TIER_DUEL_RANGES instead of guessing.
    print("\nDUEL_map percentile bins (these are what TIER_DUEL_RANGES should match):")
    duel_sorted = sorted(duel)
    n = len(duel_sorted)
    for pct in (10, 25, 33, 40, 50, 60, 66, 75, 90, 95):
        idx = min(n - 1, (n * pct) // 100)
        print(f"  p{pct:>2}: {duel_sorted[idx]:.2f}")

    print("\nLength (sec) distribution — Marathon needs drain ≥ 600s:")
    lens = [r.length for r in rows if r.length and r.length > 0]
    if lens:
        ql = _quantiles([float(x) for x in lens])
        print(f"  n={ql['n']}  mean={ql['mean']:.0f}s  p50={ql['median']:.0f}s  "
              f"p75={ql['p75']:.0f}s  max={ql['max']:.0f}s")
        for thresh in (180, 240, 300, 420, 600, 900):
            ge = sum(1 for x in lens if x >= thresh)
            print(f"  ≥ {thresh:>4}s: {ge:5d} maps  ({100*ge/len(lens):.1f}%)")


def report_tier_simulation(rows: list[MapRow]) -> None:
    """Dry-run the generator against current TIER_DUEL_RANGES & rules.

    For each tier:
      - count eligible maps in DUEL range
      - sample 9 picks (seeded), count assign_bounty_type results
    Numbers reveal which tiers are starved and which types disappear.
    """
    print("\n══════ TIER GENERATOR DRY-RUN ══════")
    print("Eligible maps per tier + simulated 9-pick bounty type distribution.\n")

    # Pull live config + rules without database (the rows are CSV/in-memory).
    try:
        from services.bounty.tier_rules import (
            TIER_DUEL_RANGES, assign_bounty_type, BOUNTY_TYPE_RULES,
            pick_for_tier,
        )
    except Exception as e:
        print(f"(cannot import tier_rules: {e})")
        return

    # Build a tiny shim object exposing the attrs the rules read.
    class _Shim:
        __slots__ = (
            "aim_stars", "speed_stars", "acc_stars", "cons_stars",
            "star_rating", "length", "drain_time", "beatmap_id",
        )
        def __init__(self, r: MapRow) -> None:
            self.aim_stars = r.aim_stars
            self.speed_stars = r.speed_stars
            self.acc_stars = r.acc_stars
            self.cons_stars = r.cons_stars
            self.star_rating = r.star_rating
            self.length = r.length
            self.drain_time = r.length
            self.beatmap_id = r.beatmap_id

    def _duel(r: MapRow) -> float:
        vs = (r.aim_stars, r.speed_stars, r.acc_stars, r.cons_stars)
        if any(v is None for v in vs):
            return -1.0
        return sum(vs) / 4.0  # type: ignore[arg-type]

    print(f"Active rules (in order): {[r.name for r in BOUNTY_TYPE_RULES]}\n")
    print(f"Current TIER_DUEL_RANGES: {TIER_DUEL_RANGES}\n")

    import random

    # Pre-build shims once; both eligibility check and pick_for_tier work
    # against the same list.
    all_shims = [_Shim(r) for r in rows]

    for tier, (lo, hi) in TIER_DUEL_RANGES.items():
        eligible_shims = [s for s in all_shims
                          if all(v is not None for v in
                                 (s.aim_stars, s.speed_stars, s.acc_stars, s.cons_stars))
                          and lo <= (s.aim_stars + s.speed_stars + s.acc_stars + s.cons_stars) / 4.0 < hi]

        type_full: dict[str, int] = {}
        for s in eligible_shims:
            t, _ = assign_bounty_type(s, tier)
            type_full[t] = type_full.get(t, 0) + 1

        # Real picker — averaged over 5 seeded runs so a single unlucky seed
        # doesn't hide the stratifier's effect.
        type_pick_avg: dict[str, float] = {}
        runs = 5
        for seed in range(42, 42 + runs):
            random.seed(seed)
            picks = pick_for_tier(eligible_shims, tier, n=9)
            for s in picks:
                t, _ = assign_bounty_type(s, tier)
                type_pick_avg[t] = type_pick_avg.get(t, 0.0) + 1.0 / runs

        print(f"  [{tier:4s}] DUEL ∈ [{lo:.2f}, {hi:.2f})  eligible={len(eligible_shims)}")
        if not eligible_shims:
            print("      (NONE — tier is starved)\n")
            continue
        print("      full-pool type histogram:")
        for t in ("Marathon", "SS", "Accuracy", "Metronome", "Mod", "Pass", "First FC"):
            c = type_full.get(t, 0)
            if c > 0:
                print(f"        {t:10s} {c:5d}  ({100*c/len(eligible_shims):.1f}%)")
        print("      9-pick stratified avg (5 runs, real pick_for_tier):")
        for t in ("Marathon", "SS", "Accuracy", "Metronome", "Mod", "Pass", "First FC"):
            c = type_pick_avg.get(t, 0.0)
            if c > 0:
                print(f"        {t:10s} {c:.1f}")
        print()


def report_ambiguous_maps(rows: list[MapRow], top_n: int = 15) -> None:
    print(f"\n══════ TOP-{top_n} AMBIGUOUS CLASSIFICATIONS ══════")
    print("Maps where top1 − top2 ≤ 0.3★. These get whatever map_type the noise picks.\n")
    ambiguous = []
    for r in rows:
        vs = {
            "aim": r.aim_stars, "speed": r.speed_stars,
            "acc": r.acc_stars, "cons": r.cons_stars,
        }
        if any(v is None for v in vs.values()):
            continue
        sv = sorted(vs.items(), key=lambda kv: kv[1], reverse=True)
        margin = sv[0][1] - sv[1][1]
        if margin <= 0.3:
            ambiguous.append((margin, r, sv))
    ambiguous.sort(key=lambda x: x[0])
    if not ambiguous:
        print("(none — all classifications are confident)")
        return

    print(f"Found {len(ambiguous)} ambiguous maps total. Showing top {top_n}:\n")
    for margin, r, sv in ambiguous[:top_n]:
        stars_str = ", ".join(f"{a}={s:.2f}" for a, s in sv)
        print(f"  bid={r.beatmap_id:>8}  SR={r.star_rating:.2f}  "
              f"margin={margin:.2f}  picked={r.map_type:<5}  "
              f"[{stars_str}]")
        print(f"     {r.title}")


# ── Entry ───────────────────────────────────────────────────────────────────

async def main() -> None:
    if len(sys.argv) > 1:
        path = sys.argv[1]
        print(f"Loading from CSV: {path}")
        rows = load_csv(path)
    else:
        print("Loading from configured DATABASE_URL (no CSV path provided)")
        rows = await load_db()

    if not rows:
        print("No rows loaded. Aborting.")
        return

    report_coverage(rows)
    report_distributions(rows)
    report_map_type(rows)
    report_argmax_margin(rows)
    report_correlations(rows)
    report_type_sr_histogram(rows)
    report_duel_and_length(rows)
    report_tier_simulation(rows)
    report_ambiguous_maps(rows)

    print("\n══════ INTERPRETATION HINTS ══════")
    print("H1 (api blend dominates):  if  aim_stars↔api_aim_diff  r ≥ 0.85, blend is winning.")
    print("H2 (cons_mult bias):       if  'cons' share at SR 7+ > 35%, cons gets too much.")
    print("H3 (argmax noise):         if  margin<0.5★  > 30%, need margin-based 'mixed' tag.")


if __name__ == "__main__":
    asyncio.run(main())
