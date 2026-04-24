"""
Nightly ML training job for BSK map skill weights.

Schedule: runs at 02:00, stops by 05:00 (hard deadline).
Reads match history from bsk_duel_rounds + bsk_ratings,
trains a Ridge regression per skill component,
updates w_aim/w_speed/w_acc/w_cons in bsk_map_pool.

Usage:
    from tasks.bsk_ml_trainer import run_nightly_training
    asyncio.create_task(run_nightly_training())
"""

import asyncio
import time
from datetime import datetime, timezone

from utils.logger import get_logger

logger = get_logger("tasks.bsk_ml_trainer")

HARD_DEADLINE_SECONDS = 3 * 3600  # 3 hours max
MIN_ROUNDS_FOR_TRAINING = 50       # don't train without enough data
MIN_ROUNDS_PER_MAP = 3             # skip maps with too few rounds


async def run_nightly_training() -> dict:
    """
    Main entry point. Returns summary dict.
    Designed to be called as an asyncio task.
    """
    start_ts = time.monotonic()
    logger.info("BSK ML training started")

    try:
        result = await asyncio.wait_for(
            _train(),
            timeout=HARD_DEADLINE_SECONDS,
        )
        elapsed = time.monotonic() - start_ts
        logger.info(f"BSK ML training finished in {elapsed:.0f}s: {result}")
        return result
    except asyncio.TimeoutError:
        logger.warning("BSK ML training hit hard deadline, stopping")
        return {"status": "timeout"}
    except Exception as e:
        logger.error(f"BSK ML training error: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def _train() -> dict:
    from db.database import AsyncSessionFactory
    from db.models.bsk_map_pool import BskMapPool
    from db.models.bsk_duel_round import BskDuelRound
    from db.models.bsk_rating import BskRating
    from db.models.user import User
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        # Load all completed rounds with scores
        rounds = (await session.execute(
            select(BskDuelRound).where(
                BskDuelRound.status == "completed",
                BskDuelRound.player1_composite.isnot(None),
                BskDuelRound.player2_composite.isnot(None),
            )
        )).scalars().all()

        if len(rounds) < MIN_ROUNDS_FOR_TRAINING:
            logger.info(f"Not enough data: {len(rounds)} rounds (need {MIN_ROUNDS_FOR_TRAINING})")
            return {"status": "skipped", "rounds": len(rounds)}

        # Load all ratings keyed by user_id + mode
        ratings_raw = (await session.execute(
            select(BskRating).where(BskRating.mode == "ranked")
        )).scalars().all()
        ratings = {r.user_id: r for r in ratings_raw}

        # Load all maps
        maps_raw = (await session.execute(
            select(BskMapPool).where(BskMapPool.enabled == True)
        )).scalars().all()
        maps = {m.beatmap_id: m for m in maps_raw}

    # Build training data per map
    # For each map: collect (player_skill_diff, round_winner) pairs per component
    map_data: dict[int, list[dict]] = {}

    for rnd in rounds:
        if rnd.beatmap_id not in maps:
            continue
        r1 = ratings.get(rnd.player1_user_id)
        r2 = ratings.get(rnd.player2_user_id)
        if not r1 or not r2:
            continue

        winner = 1 if rnd.player1_composite > rnd.player2_composite else 2
        actual = 1.0 if winner == 1 else 0.0

        entry = {
            "actual": actual,
            "diff_aim":   r1.mu_aim   - r2.mu_aim,
            "diff_speed": r1.mu_speed - r2.mu_speed,
            "diff_acc":   r1.mu_acc   - r2.mu_acc,
            "diff_cons":  r1.mu_cons  - r2.mu_cons,
        }
        map_data.setdefault(rnd.beatmap_id, []).append(entry)

    # Train per-map weights using logistic residuals
    updated = 0
    skipped = 0

    async with AsyncSessionFactory() as session:
        for beatmap_id, entries in map_data.items():
            if len(entries) < MIN_ROUNDS_PER_MAP:
                skipped += 1
                continue

            weights = _estimate_weights_from_residuals(entries)
            if weights is None:
                skipped += 1
                continue

            map_entry = (await session.execute(
                select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
            )).scalar_one_or_none()

            if not map_entry:
                continue

            # Blend ML weights with existing heuristic (70% ML, 30% heuristic)
            map_entry.w_aim   = round(0.7 * weights["aim"]   + 0.3 * map_entry.w_aim,   3)
            map_entry.w_speed = round(0.7 * weights["speed"] + 0.3 * map_entry.w_speed, 3)
            map_entry.w_acc   = round(0.7 * weights["acc"]   + 0.3 * map_entry.w_acc,   3)
            map_entry.w_cons  = round(0.7 * weights["cons"]  + 0.3 * map_entry.w_cons,  3)

            from services.bsk.osu_parser import map_type_from_weights
            map_entry.map_type = map_type_from_weights({
                "aim": map_entry.w_aim, "speed": map_entry.w_speed,
                "acc": map_entry.w_acc, "cons": map_entry.w_cons,
            })
            updated += 1

        await session.commit()

    return {
        "status": "ok",
        "rounds_used": len(rounds),
        "maps_updated": updated,
        "maps_skipped": skipped,
    }


def _estimate_weights_from_residuals(entries: list[dict]) -> dict | None:
    """
    For each skill component, compute how well the skill difference
    predicts the actual round outcome. Higher correlation → higher weight.

    Uses simple correlation coefficient as a proxy for importance.
    No external ML libraries needed — pure Python.
    """
    import math

    components = ["aim", "speed", "acc", "cons"]
    correlations = {}

    actual_vals = [e["actual"] for e in entries]
    mean_actual = sum(actual_vals) / len(actual_vals)

    for comp in components:
        diffs = [e[f"diff_{comp}"] for e in entries]
        mean_diff = sum(diffs) / len(diffs)

        # Pearson correlation between skill diff and actual outcome
        num = sum((d - mean_diff) * (a - mean_actual) for d, a in zip(diffs, actual_vals))
        den_d = math.sqrt(sum((d - mean_diff) ** 2 for d in diffs) + 1e-9)
        den_a = math.sqrt(sum((a - mean_actual) ** 2 for a in actual_vals) + 1e-9)
        corr = abs(num / (den_d * den_a))
        correlations[comp] = corr

    total = sum(correlations.values())
    if total < 1e-9:
        return None

    return {k: round(v / total, 3) for k, v in correlations.items()}
