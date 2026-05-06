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
RANKED_MULTIPLIER_STEP = 2
RANKED_MULTIPLIER_INC = 0.375
RANKED_MULTIPLIER_CAP = 3.75
# Ban phases happen *before* these round numbers in ranked mode.
RANKED_BAN_PHASE_ROUNDS = (1, 4, 8, 12, 16)

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
