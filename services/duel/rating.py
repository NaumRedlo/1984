"""Single-track TrueSkill rating for 1v1 duels.

One Gaussian skill belief per (user, mode): ``mu`` (mean) and ``sigma``
(uncertainty), updated once per duel from the binary win/loss outcome via the
``trueskill`` library.  The leaderboard / division layer reads
``conservative = mu - DUEL_CONSERVATIVE_K*sigma`` (on :class:`DuelRating`).

The environment is the stock TrueSkill model scaled ×90 from its defaults
(25 / 8.33 / 4.17 / 0.083) so ratings live on the 0..~5700 scale the
division thresholds (``DUEL_DIVISION_THRESHOLDS``, Rhythmus I = 5000) already
use.  Outcomes are binary today; round margin (3:0 vs 3:2) can later be folded
in via partial-play weights — that extensibility is why we use the library.
"""

from __future__ import annotations

from datetime import datetime, timezone

import trueskill
from sqlalchemy import select

from db.database import get_db_session
from db.models.duel_rating import DuelRating
from utils.hp_calculator import get_division_for_conservative
from utils.logger import get_logger

logger = get_logger("duel.rating")

# ── TrueSkill environment (stock model, scaled ×90) ──────────────────────────
DUEL_TS_MU0 = 2250.0
DUEL_TS_SIGMA0 = 750.0
DUEL_TS_BETA = 375.0           # skill-class width (≈ sigma0 / 2)
DUEL_TS_TAU = 7.5             # additive dynamics per game (≈ sigma0 / 100)

_TS = trueskill.TrueSkill(
    mu=DUEL_TS_MU0,
    sigma=DUEL_TS_SIGMA0,
    beta=DUEL_TS_BETA,
    tau=DUEL_TS_TAU,
    draw_probability=0.0,      # duels always resolve a winner (tiebreak map)
)

PLACEMENT_MATCHES = 10


# ── pp → starting mu seed ────────────────────────────────────────────────────
# Piecewise-linear: maps osu! pp to an initial mu so a fresh rating starts near
# the player's real level instead of the flat mu0.  sigma stays at sigma0, so
# the conservative score still ramps up only as games are played.
_PP_MU_CURVE: list[tuple[float, float]] = [
    (0,      1350.0),
    (1000,   1650.0),
    (2000,   1950.0),
    (3000,   2175.0),
    (4000,   2400.0),
    (5000,   2625.0),
    (6000,   2850.0),
    (8000,   3225.0),
    (10000,  3600.0),
    (13000,  4050.0),
    (18000,  4500.0),
    (25000,  5100.0),
    (35000,  5700.0),   # cap
]


def starting_mu_from_pp(pp: float) -> float:
    """Initial mu seed from osu! pp (piecewise-linear, clamped to the curve)."""
    if pp <= 0:
        return _PP_MU_CURVE[0][1]
    if pp >= _PP_MU_CURVE[-1][0]:
        return _PP_MU_CURVE[-1][1]
    for i in range(len(_PP_MU_CURVE) - 1):
        pp0, mu0 = _PP_MU_CURVE[i]
        pp1, mu1 = _PP_MU_CURVE[i + 1]
        if pp0 <= pp <= pp1:
            t = (pp - pp0) / (pp1 - pp0)
            return mu0 + t * (mu1 - mu0)
    return _PP_MU_CURVE[-1][1]


def rating_to_sr(rating_value: float) -> float:
    """Map a mu-scale rating to a target star rating for pool building.

    The duel manager averages both players' mu and asks the map selector for a
    pool around this SR.  Calibrated so mu0 (2250) ≈ 4.5★ and the top of the
    curve saturates at 10★.
    """
    sr = rating_value / 500.0
    return max(1.5, min(10.0, sr))


# ── rating lifecycle ─────────────────────────────────────────────────────────
def _seed_rating(rating: DuelRating, pp: float) -> None:
    rating.mu = starting_mu_from_pp(pp)
    rating.sigma = DUEL_TS_SIGMA0
    rating.peak_mu = rating.mu


async def get_or_create_rating(user_id: int, mode: str, player_pp: float = 0.0) -> DuelRating:
    async with get_db_session() as session:
        rating = (await session.execute(
            select(DuelRating).where(
                DuelRating.user_id == user_id,
                DuelRating.mode == mode,
            )
        )).scalar_one_or_none()
        if rating is None:
            rating = DuelRating(user_id=user_id, mode=mode)
            _seed_rating(rating, player_pp)
            session.add(rating)
            await session.commit()
            await session.refresh(rating)
        return rating


def _apply_outcome(winner: DuelRating, loser: DuelRating) -> None:
    """In-place TrueSkill update for a decided 1v1 (winner beats loser)."""
    rw = _TS.create_rating(mu=winner.mu, sigma=winner.sigma)
    rl = _TS.create_rating(mu=loser.mu, sigma=loser.sigma)
    (new_w,), (new_l,) = _TS.rate([(rw,), (rl,)], ranks=[0, 1])

    winner.mu, winner.sigma = float(new_w.mu), float(new_w.sigma)
    loser.mu, loser.sigma = float(new_l.mu), float(new_l.sigma)

    now = datetime.now(timezone.utc)
    for r in (winner, loser):
        r.games += 1
        if r.placement_matches_left > 0:
            r.placement_matches_left -= 1
        r.updated_at = now
    winner.wins += 1
    loser.losses += 1
    if winner.mu > winner.peak_mu:
        winner.peak_mu = winner.mu


async def update_ratings(
    winner_id: int,
    loser_id: int,
    mode: str,
    winner_pp: float = 0.0,
    loser_pp: float = 0.0,
) -> tuple[DuelRating, DuelRating, str, str, str, str]:
    """Apply a duel result and return both ratings plus old/new divisions.

    Returns ``(winner, loser, w_old_div, w_new_div, l_old_div, l_new_div)``.
    """
    async with get_db_session() as session:
        w = (await session.execute(
            select(DuelRating).where(DuelRating.user_id == winner_id, DuelRating.mode == mode)
        )).scalar_one_or_none()
        l = (await session.execute(
            select(DuelRating).where(DuelRating.user_id == loser_id, DuelRating.mode == mode)
        )).scalar_one_or_none()

        if w is None:
            w = DuelRating(user_id=winner_id, mode=mode)
            _seed_rating(w, winner_pp)
            session.add(w)
        if l is None:
            l = DuelRating(user_id=loser_id, mode=mode)
            _seed_rating(l, loser_pp)
            session.add(l)
        await session.flush()

        w_old_div = get_division_for_conservative(w.conservative)
        l_old_div = get_division_for_conservative(l.conservative)

        _apply_outcome(w, l)

        await session.commit()
        await session.refresh(w)
        await session.refresh(l)

        w_new_div = get_division_for_conservative(w.conservative)
        l_new_div = get_division_for_conservative(l.conservative)

        logger.info(
            f"update_ratings: mode={mode} winner={winner_id} "
            f"(μ{w.mu:.0f}/σ{w.sigma:.0f} {w_old_div}→{w_new_div}) "
            f"loser={loser_id} (μ{l.mu:.0f}/σ{l.sigma:.0f} {l_old_div}→{l_new_div})"
        )
        return w, l, w_old_div, w_new_div, l_old_div, l_new_div
