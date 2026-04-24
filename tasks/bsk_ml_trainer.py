"""
Nightly ML training job for BSK map skill weights.

Schedule: runs at 02:00, stops by 05:00 (hard deadline).
Reads match history from bsk_duel_rounds + bsk_ratings,
trains a Ridge regression per skill component,
updates w_aim/w_speed/w_acc/w_cons in bsk_map_pool.
Saves run history to bsk_ml_runs.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

logger = get_logger("tasks.bsk_ml_trainer")

HARD_DEADLINE_SECONDS = 3 * 3600  # 3 hours max
MIN_ROUNDS_FOR_TRAINING = 50
MIN_ROUNDS_PER_MAP = 3

# Global state for monitoring
_current_task: Optional[asyncio.Task] = None
_paused = False
_progress: dict = {}


def get_progress() -> dict:
    """Return current training progress snapshot."""
    return dict(_progress)


def is_running() -> bool:
    return _current_task is not None and not _current_task.done()


def is_paused() -> bool:
    return _paused


def pause_training():
    global _paused
    _paused = True


def resume_training():
    global _paused
    _paused = False


def cancel_training():
    global _current_task, _paused
    _paused = False
    if _current_task and not _current_task.done():
        _current_task.cancel()


async def run_nightly_training(triggered_by: str = "scheduler") -> dict:
    global _current_task, _progress
    start_ts = time.monotonic()
    logger.info(f"BSK ML training started (triggered_by={triggered_by})")
    _progress = {"status": "running", "triggered_by": triggered_by, "maps_updated": 0, "maps_skipped": 0, "rounds_used": 0}

    try:
        _current_task = asyncio.current_task()
        result = await asyncio.wait_for(
            _train(),
            timeout=HARD_DEADLINE_SECONDS,
        )
        elapsed = time.monotonic() - start_ts
        logger.info(f"BSK ML training finished in {elapsed:.0f}s: {result}")
    except asyncio.CancelledError:
        result = {"status": "cancelled", "rounds_used": _progress.get("rounds_used", 0),
                  "maps_updated": _progress.get("maps_updated", 0), "maps_skipped": _progress.get("maps_skipped", 0)}
        logger.info("BSK ML training cancelled")
    except asyncio.TimeoutError:
        result = {"status": "timeout", "rounds_used": _progress.get("rounds_used", 0),
                  "maps_updated": _progress.get("maps_updated", 0), "maps_skipped": _progress.get("maps_skipped", 0)}
        logger.warning("BSK ML training hit hard deadline, stopping")
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        logger.error(f"BSK ML training error: {e}", exc_info=True)
    finally:
        _current_task = None
        _progress = {}

    await _save_run(result, triggered_by)
    return result


async def _save_run(result: dict, triggered_by: str) -> None:
    try:
        from db.database import AsyncSessionFactory
        from db.models.bsk_ml_run import BskMlRun
        async with AsyncSessionFactory() as session:
            run = BskMlRun(
                ran_at=datetime.now(timezone.utc),
                rounds_used=result.get("rounds_used", 0),
                maps_updated=result.get("maps_updated", 0),
                maps_skipped=result.get("maps_skipped", 0),
                status=result.get("status", "ok"),
                triggered_by=triggered_by,
                notes=result.get("error"),
            )
            session.add(run)
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to save ML run history: {e}")


async def _train() -> dict:
    from db.database import AsyncSessionFactory
    from db.models.bsk_map_pool import BskMapPool
    from db.models.bsk_duel_round import BskDuelRound
    from db.models.bsk_rating import BskRating
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        rounds = (await session.execute(
            select(BskDuelRound).where(
                BskDuelRound.status == "completed",
                BskDuelRound.player1_composite.isnot(None),
                BskDuelRound.player2_composite.isnot(None),
            )
        )).scalars().all()

        if len(rounds) < MIN_ROUNDS_FOR_TRAINING:
            logger.info(f"Not enough data: {len(rounds)} rounds (need {MIN_ROUNDS_FOR_TRAINING})")
            return {"status": "skipped", "rounds_used": len(rounds), "maps_updated": 0, "maps_skipped": 0}

        ratings_raw = (await session.execute(
            select(BskRating).where(BskRating.mode == "ranked")
        )).scalars().all()
        ratings = {r.user_id: r for r in ratings_raw}

        maps_raw = (await session.execute(
            select(BskMapPool).where(BskMapPool.enabled == True)
        )).scalars().all()
        maps = {m.beatmap_id: m for m in maps_raw}

    map_data: dict[int, list[dict]] = {}
    for rnd in rounds:
        if rnd.beatmap_id not in maps:
            continue
        r1 = ratings.get(rnd.player1_user_id)
        r2 = ratings.get(rnd.player2_user_id)
        if not r1 or not r2:
            continue
        actual = 1.0 if rnd.player1_composite > rnd.player2_composite else 0.0
        map_data.setdefault(rnd.beatmap_id, []).append({
            "actual": actual,
            "diff_aim":   r1.mu_aim   - r2.mu_aim,
            "diff_speed": r1.mu_speed - r2.mu_speed,
            "diff_acc":   r1.mu_acc   - r2.mu_acc,
            "diff_cons":  r1.mu_cons  - r2.mu_cons,
        })

    updated = 0
    skipped = 0
    total_maps = len(map_data)

    async with AsyncSessionFactory() as session:
        for i, (beatmap_id, entries) in enumerate(map_data.items()):
            # Pause support — yield control and wait until resumed
            while _paused:
                _progress["status"] = "paused"
                await asyncio.sleep(1)
            _progress.update({"status": "running", "maps_updated": updated,
                               "maps_skipped": skipped, "maps_total": total_maps, "maps_done": i})

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

    return {"status": "ok", "rounds_used": len(rounds), "maps_updated": updated, "maps_skipped": skipped}


def _estimate_weights_from_residuals(entries: list[dict]) -> dict | None:
    import math
    components = ["aim", "speed", "acc", "cons"]
    correlations = {}
    actual_vals = [e["actual"] for e in entries]
    mean_actual = sum(actual_vals) / len(actual_vals)

    for comp in components:
        diffs = [e[f"diff_{comp}"] for e in entries]
        mean_diff = sum(diffs) / len(diffs)
        num = sum((d - mean_diff) * (a - mean_actual) for d, a in zip(diffs, actual_vals))
        den_d = math.sqrt(sum((d - mean_diff) ** 2 for d in diffs) + 1e-9)
        den_a = math.sqrt(sum((a - mean_actual) ** 2 for a in actual_vals) + 1e-9)
        correlations[comp] = abs(num / (den_d * den_a))

    total = sum(correlations.values())
    if total < 1e-9:
        return None
    return {k: round(v / total, 3) for k, v in correlations.items()}
