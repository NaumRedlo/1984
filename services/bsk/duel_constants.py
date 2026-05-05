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
RANKED_MULTIPLIER_INC = 0.25
RANKED_MULTIPLIER_CAP = 2.0
# Ban phases happen *before* these round numbers in ranked mode.
RANKED_BAN_PHASE_ROUNDS = (1, 5, 10, 15, 20)
# Ranked pool target SR is offset above the higher of the two players' SR
# so maps stay at the top of their level.
RANKED_TARGET_SR_OFFSET = 0.5
