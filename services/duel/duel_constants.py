"""Constants for the duel lifecycle (simple Bo-N, auto pool, TrueSkill)."""

ACCEPT_TIMEOUT_MINUTES = 5

# Round monitoring
SCORE_POLL_INTERVAL = 15        # seconds between get_match polls while a map is live
MAP_READY_COUNTDOWN = 90        # seconds to wait for "all ready" before force-start
ROUND_FORFEIT_BUFFER_MIN = 12   # extra minutes after map length → round is void
MAX_MONITOR_HOURS = 3           # whole-duel watchdog

# Best-of by mode: total rounds (Bo) + rounds needed to win (TO).
POOL_SIZE_CASUAL = 5
POOL_SIZE_RANKED = 10
WIN_TARGET_CASUAL = 3
WIN_TARGET_RANKED = 6

# The auto-built map pool / pick card is always a fixed 6-map 2×3 grid,
# independent of the Bo format above. The round engine resolves `win_target`
# against this pool (leader-wins-on-exhaust, then capped tiebreakers), so the
# pool size and the Bo number need not match.
DUEL_POOL_MAPS = 6

# Cap on sudden-death tiebreak maps if the pool ends level (all voids/ties).
MAX_TIEBREAKERS = 5


def pool_size_for(mode: str) -> int:
    return POOL_SIZE_RANKED if mode == 'ranked' else POOL_SIZE_CASUAL


def win_target_for(mode: str) -> int:
    return WIN_TARGET_RANKED if mode == 'ranked' else WIN_TARGET_CASUAL
