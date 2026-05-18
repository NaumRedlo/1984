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

from typing import Optional


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
