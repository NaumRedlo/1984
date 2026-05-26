"""Render bounty UI cards to /tmp/ for visual inspection.

Outputs:
  /tmp/bounty_tier_<TIER>_p1.png  — first 5 entries of each tier from /bli
  /tmp/bounty_tier_<TIER>_p2.png  — second 4 entries (if any)
  /tmp/bounty_detail.png           — compact /bde card for one sample bounty

Source of data, in priority order:
  1. Active WeeklyBountyPool in the live DB (real bot data).
  2. Synthetic 9-per-tier dummy set so the script is runnable on empty DBs.

Usage:
    python3 -m scripts.render_bounty_demo

The script never writes to the DB — it only reads.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select


# ── Helpers ────────────────────────────────────────────────────────────────

def _format_conditions_compact(b: Any) -> list[str]:
    lines: list[str] = []
    if getattr(b, "min_accuracy", None) is not None:
        lines.append(f"🎯 Точность ≥ {b.min_accuracy}%")
    if getattr(b, "max_misses", None) is not None:
        lines.append("FC (0 миссов)" if b.max_misses == 0 else f"❌ Миссов ≤ {b.max_misses}")
    if getattr(b, "required_mods", None):
        lines.append(f"🎚 Моды: {b.required_mods}")
    cond_raw = getattr(b, "conditions", None)
    if cond_raw:
        try:
            jc = json.loads(cond_raw)
            if isinstance(jc, dict):
                if "max_ur" in jc:
                    lines.append(f"⏱ UR ≤ {jc['max_ur']} ms")
                if "min_combo_pct" in jc:
                    lines.append(f"🔗 Комбо ≥ {float(jc['min_combo_pct'])*100:.0f}%")
        except Exception:
            pass
    if getattr(b, "min_rank", None):
        lines.append(f"🥇 Ранг ≥ {b.min_rank}")
    return lines or ["Без ограничений"]


def _format_conditions_latin(b: Any) -> str:
    parts: list[str] = []
    if getattr(b, "max_misses", None) == 0:
        parts.append("FC")
    elif getattr(b, "max_misses", None) is not None:
        parts.append(f"<={b.max_misses} miss")
    if getattr(b, "min_accuracy", None) is not None:
        a = float(b.min_accuracy)
        parts.append("SS" if a >= 100 else f"Acc {a:.1f}+")
    if getattr(b, "required_mods", None):
        parts.append("+" + b.required_mods.replace(",", "+").upper())
    cond_raw = getattr(b, "conditions", None)
    if cond_raw:
        try:
            jc = json.loads(cond_raw)
            if isinstance(jc, dict):
                if "max_ur" in jc:
                    parts.append(f"UR<={jc['max_ur']}ms")
                if "min_combo_pct" in jc:
                    parts.append(f"Cmb>={float(jc['min_combo_pct'])*100:.0f}%")
        except Exception:
            pass
    return "   ".join(parts)


def _bounty_to_entry(b: Any, sub_count: int = 0) -> dict:
    dl = b.deadline.strftime("%d.%m %H:%M") if getattr(b, "deadline", None) else "--"
    return {
        "bounty_id":         b.bounty_id,
        "bounty_type":       b.bounty_type or "First FC",
        "tier":              b.tier or "Open",
        "title":             b.title,
        "beatmap_title":     b.beatmap_title,
        "beatmapset_id":     b.beatmapset_id,
        "star_rating":       float(b.star_rating or 0.0),
        "drain_time":        b.drain_time,
        "mapper_name":       b.mapper_name,
        "deadline":          dl,
        "participant_count": sub_count,
        "max_participants":  b.max_participants,
        "conditions":        _format_conditions_compact(b),
        "conditions_latin":  _format_conditions_latin(b),
    }


# ── Synthetic fallback ────────────────────────────────────────────────────

def _synthetic_pool() -> dict[str, list[dict]]:
    """9 bounties per tier covering every bounty type."""
    types_per_tier = {
        "C":    ["Mod", "Mod", "Pass", "Metronome", "Accuracy", "Marathon", "First FC", "First FC", "Mod"],
        "B":    ["Accuracy", "SS", "Metronome", "Pass", "Mod", "Marathon", "First FC", "Pass", "Accuracy"],
        "A":    ["SS", "SS", "Marathon", "Accuracy", "Pass", "Metronome", "First FC", "Pass", "Marathon"],
        "Open": ["First FC", "Pass", "Mod", "Accuracy", "Metronome", "SS", "Marathon", "First FC", "Pass"],
    }
    sr_per_tier = {"C": 3.6, "B": 5.4, "A": 7.1, "Open": 4.8}
    sample_titles = [
        "xi - Blue Zenith [Fullerene]",
        "Camellia - Exit This Earth's Atomosphere [Final]",
        "Imperial Circus Dead Decadence - Yomi yori Kikoyu, Koukoku no Tou to Honoo no Shoujo. [Tsuki]",
        "USAO - Boss Rush 2 [Boss Battle]",
        "uma - Yoiyami Hanabi [Sakura]",
        "DragonForce - Through the Fire and Flames [Marathon]",
        "Daisuke Aoki - hostile, take an alaska [PAIN]",
        "Ricky Montgomery - Line Without a Hook [Falling]",
        "BLACKPINK - Pink Venom (Sped Up) [Black Mamba]",
    ]
    mappers = ["Sotarks", "pishifat", "Mafiamaster", "Monstrata", "Nathan", "Mismagius", "Reform", "Lasse", "Doomsday"]

    by_tier: dict[str, list[dict]] = {}
    for tier, types in types_per_tier.items():
        entries = []
        for i, btype in enumerate(types):
            sr_jitter = sr_per_tier[tier] + (i - 4) * 0.18
            drain = 600 if btype == "Marathon" else 90 + i * 35
            # synthetic conditions per type
            conditions = []
            cond_latin_parts = []
            if btype == "SS":
                conditions = ["🎯 Точность ≥ 100.0%", "FC (0 миссов)"]
                cond_latin_parts = ["SS", "FC"]
            elif btype == "Accuracy":
                conditions = ["🎯 Точность ≥ 98.5%"]
                cond_latin_parts = ["Acc 98.5+"]
            elif btype == "Metronome":
                conditions = ["⏱ UR ≤ 75 ms"]
                cond_latin_parts = ["UR<=75ms"]
            elif btype == "Mod":
                mod = ["HR", "HD", "DT"][i % 3]
                conditions = [f"🎚 Моды: {mod}"]
                cond_latin_parts = [f"+{mod}"]
            elif btype == "Marathon":
                conditions = ["🔗 Комбо ≥ 80%"]
                cond_latin_parts = ["Cmb>=80%"]
            elif btype == "Pass":
                conditions = ["Без ограничений"]
            else:  # First FC
                conditions = ["FC (0 миссов)"]
                cond_latin_parts = ["FC"]
            entries.append({
                "bounty_id":         f"DEMO/{tier}-{i+1:02d}",
                "bounty_type":       btype,
                "tier":              tier,
                "title":             f"{btype} · {tier}",
                "beatmap_title":     sample_titles[i % len(sample_titles)],
                "beatmapset_id":     None,  # no cover; renderer falls back to flat tint
                "star_rating":       max(0.5, sr_jitter),
                "drain_time":        drain,
                "mapper_name":       mappers[i % len(mappers)],
                "deadline":          "01.06 23:59",
                "participant_count": (i * 3) % 7,
                "max_participants":  None,
                "conditions":        conditions,
                "conditions_latin":  "   ".join(cond_latin_parts),
            })
        by_tier[tier] = entries
    return by_tier


# ── Live data loader ───────────────────────────────────────────────────────

async def _load_live_pool() -> dict[str, list[dict]] | None:
    """Return {tier: [entries]} for the active weekly pool, or None if empty."""
    try:
        from db.database import get_db_session
        from db.models.bounty import Bounty, Submission
        from sqlalchemy import func
    except Exception as e:
        print(f"(cannot import models: {e})")
        return None

    async with get_db_session() as s:
        try:
            rows = (await s.execute(
                select(Bounty)
                .where(Bounty.status == "active")
                .order_by(Bounty.tier.asc().nulls_last(), Bounty.created_at.desc())
            )).scalars().all()
        except Exception as e:
            print(f"(cannot read bounties table: {e}; using synthetic data)")
            return None
        if not rows:
            return None

        counts_stmt = (
            select(Submission.bounty_id, func.count(Submission.id))
            .where(Submission.bounty_id.in_([b.bounty_id for b in rows]))
            .group_by(Submission.bounty_id)
        )
        sub_counts = dict((await s.execute(counts_stmt)).all())

    by_tier: dict[str, list[dict]] = {t: [] for t in ("C", "B", "A", "Open")}
    for b in rows:
        tier = b.tier if b.tier in by_tier else "Open"
        by_tier[tier].append(_bounty_to_entry(b, sub_counts.get(b.bounty_id, 0)))
    return by_tier


# ── Render ─────────────────────────────────────────────────────────────────

async def _render_all(by_tier: dict[str, list[dict]], outdir: Path) -> None:
    from services.image.core import CardRenderer
    renderer = CardRenderer()
    outdir.mkdir(parents=True, exist_ok=True)

    for tier in ("C", "B", "A", "Open"):
        entries = by_tier.get(tier, [])
        if not entries:
            print(f"[{tier}] empty — skipping")
            continue
        # Page 1
        buf = await renderer.generate_bounty_tier_card_async(tier, entries[:5], offset=0)
        p1 = outdir / f"bounty_tier_{tier}_p1.png"
        p1.write_bytes(buf.getvalue())
        print(f"[{tier}] page 1  ({len(entries[:5])} rows) → {p1}")
        # Page 2 if needed
        if len(entries) > 5:
            buf2 = await renderer.generate_bounty_tier_card_async(tier, entries[5:10], offset=5)
            p2 = outdir / f"bounty_tier_{tier}_p2.png"
            p2.write_bytes(buf2.getvalue())
            print(f"[{tier}] page 2  ({len(entries[5:10])} rows) → {p2}")

    # Compact detail card — pick first entry from C, or whatever exists.
    sample_entry = None
    for tier in ("C", "B", "A", "Open"):
        if by_tier.get(tier):
            sample_entry = by_tier[tier][0]
            break
    if sample_entry is None:
        print("(no entries anywhere — skipping detail card)")
        return
    detail_data = {**sample_entry, "hps_preview_hp": 187}
    buf = await renderer.generate_bounty_compact_card_async(detail_data)
    p = outdir / "bounty_detail.png"
    p.write_bytes(buf.getvalue())
    print(f"[detail] sample bounty card → {p}")


# ── Entry ──────────────────────────────────────────────────────────────────

async def main() -> None:
    outdir = Path("/tmp")
    by_tier = await _load_live_pool()
    if by_tier is None:
        print("Using synthetic dummy pool (live DB had no active bounties).")
        by_tier = _synthetic_pool()
    else:
        n = sum(len(v) for v in by_tier.values())
        print(f"Using LIVE pool: {n} active bounties across tiers "
              f"{', '.join(f'{t}={len(by_tier[t])}' for t in ('C','B','A','Open'))}")

    await _render_all(by_tier, outdir)
    print("\nDone. Inspect with:\n  ls -la /tmp/bounty_*.png")


if __name__ == "__main__":
    asyncio.run(main())
