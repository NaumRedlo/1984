"""Constants used by BSK duel lifecycle."""

ACCEPT_TIMEOUT_MINUTES = 5
SCORE_POLL_INTERVAL = 15
TARGET_SCORE = 1_000_000

BAN_TIMEOUT_SECONDS = 60
MAX_BANS = 3
POOL_SIZE = 6

PICK_TIMEOUT_SECONDS = 60
MAX_MONITOR_HOURS = 2

# ── Ranked mode tuning ─────────────────────────────────────────────────────
# Higher score target compensates for the score multiplier and longer match.
TARGET_SCORE_RANKED = 2_000_000
# Hard round cap for ranked duels (BO20).
MAX_ROUNDS_RANKED = 20
# Score multiplier kicks in every N rounds, +INC, capped at CAP.
RANKED_MULTIPLIER_STEP = 4
RANKED_MULTIPLIER_INC = 0.425
RANKED_MULTIPLIER_CAP = 3.125
# Ban phases happen *before* these round numbers in ranked mode.
# Five 4-round segments (1-4, 5-8, 9-12, 13-16, 17-20) so each player picks
# exactly 2 maps per segment — pick count stays balanced regardless of who
# starts the segment.
RANKED_BAN_PHASE_ROUNDS = (1, 5, 9, 13, 17)

# ── Casual mode tuning ─────────────────────────────────────────────────────
# Hard round cap for casual duels (BO15).
MAX_ROUNDS_CASUAL = 15
# Score multiplier — every 3 rounds, +0.5, capped at 2.5×.
CASUAL_MULTIPLIER_STEP = 3
CASUAL_MULTIPLIER_INC = 0.5
CASUAL_MULTIPLIER_CAP = 2.5
# Ranked pool target SR is offset above the higher of the two players' SR
# so maps stay at the top of their level.
RANKED_TARGET_SR_OFFSET = 0.5


# ── Helpers (mode-aware, no DB/IO dependencies) ────────────────────────────

from datetime import datetime, timedelta, timezone
from typing import Optional


def _forfeit_deadline(map_length_seconds: int) -> datetime:
    """UTC deadline for a player to submit their score for the round."""
    buffer = 15 * 60  # 15 min buffer
    return datetime.now(timezone.utc) + timedelta(seconds=map_length_seconds + buffer)


def _target_score_for_mode(mode: str) -> int:
    return TARGET_SCORE_RANKED if mode == 'ranked' else TARGET_SCORE


def _ranked_round_multiplier(round_number: int) -> float:
    steps = max(0, (round_number - 1) // RANKED_MULTIPLIER_STEP)
    return min(1.0 + RANKED_MULTIPLIER_INC * steps, RANKED_MULTIPLIER_CAP)


def _casual_round_multiplier(round_number: int) -> float:
    steps = max(0, (round_number - 1) // CASUAL_MULTIPLIER_STEP)
    return min(1.0 + CASUAL_MULTIPLIER_INC * steps, CASUAL_MULTIPLIER_CAP)


def _round_multiplier_for(mode: str, round_number: int) -> float:
    if mode == 'ranked':
        return _ranked_round_multiplier(round_number)
    if mode == 'casual':
        return _casual_round_multiplier(round_number)
    return 1.0


def _max_rounds_for(mode: str) -> Optional[int]:
    if mode == 'ranked':
        return MAX_ROUNDS_RANKED
    if mode == 'casual':
        return MAX_ROUNDS_CASUAL
    return None


def _base_sr_for_duel(r1, r2, mode: str = 'casual') -> float:
    """Base star-rating for a duel from the two players' ratings.

    Uses the SUM of the four components — starting_mu_from_pp() is defined on
    the sum scale: sum / 200 = SR (e.g. sum=1000 → 5.0★).

    In ranked mode the duel SR is biased up by RANKED_TARGET_SR_OFFSET so maps
    sit at the top of the players' level rather than the average.
    """
    sum1 = r1.mu_aim + r1.mu_speed + r1.mu_acc + r1.mu_cons
    sum2 = r2.mu_aim + r2.mu_speed + r2.mu_acc + r2.mu_cons
    base = (sum1 + sum2) / 2 / 200
    if mode == 'ranked':
        base += RANKED_TARGET_SR_OFFSET
    return max(1.0, min(10.0, round(base, 1)))
