"""
BSK rating update logic.

Four skill components: aim, speed, acc, cons. Global rating exposed via
``BskRating.mu_global`` (weighted: 0.30·aim + 0.30·speed + 0.25·acc + 0.15·cons).

Ratings are updated **once per duel**, not per round. ``result`` reflects the
score share, so a 3:0 sweep moves the rating more than a 3:2 nail-biter.

K-factors (per duel):
    casual: K = 16
    ranked: K = 32
A player still in placement (``placement_matches_left > 0``) gets their delta
multiplied by ``PLACEMENT_K_MULTIPLIER`` (3×). Multiplier applies only to that
player — the calibrated opponent is unaffected, breaking strict zero-sum during
calibration on purpose.

Component dispatch:
    1. Compute a single global delta = K · (result - expected_a).
    2. Distribute it across components proportionally to ``map_weights``,
       blended with a uniform 30% baseline so specialty maps still nudge every
       skill.

Component values are clamped to ``[COMPONENT_FLOOR, COMPONENT_CEILING]``.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from db.database import get_db_session
from db.models.bsk_rating import BskRating

COMPONENT_FLOOR = 0.0
COMPONENT_CEILING = 1000.0
K_CASUAL = 16
K_RANKED = 32
C = 400.0  # scale constant for expected score

PLACEMENT_K_MULTIPLIER = 3
WEIGHT_BASELINE = 0.30  # share of delta distributed uniformly across components


def _expected(mu_a: float, mu_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((mu_b - mu_a) / C))


def _clamp(value: float) -> float:
    return max(COMPONENT_FLOOR, min(COMPONENT_CEILING, value))


def _component_share(weight: float) -> float:
    """Blend a per-component map weight with the uniform baseline.

    Pure aim-map (weight=1.0, others=0.0) becomes
        used = 0.7·1.0 + 0.3·0.25 = 0.775 for aim
        used = 0.7·0.0 + 0.3·0.25 = 0.075 for others
    Sum stays 1.0 so the global delta is preserved.
    """
    return (1.0 - WEIGHT_BASELINE) * weight + WEIGHT_BASELINE * 0.25


# ---------------------------------------------------------------------------
# Piecewise-linear calibration curve  (pp → target component SUM)
# sum / 200 = starting SR,  per_comp = sum / 4
# ---------------------------------------------------------------------------
_PP_SR_CURVE: list[tuple[float, float]] = [
    (0,      200.0),   # 1.0★
    (1000,   400.0),   # 2.0★
    (2000,   600.0),   # 3.0★
    (3000,   800.0),   # 4.0★
    (4000,   920.0),   # 4.6★
    (5000,  1080.0),   # 5.4★
    (6000,  1240.0),   # 6.2★
    (7000,  1380.0),   # 6.9★
    (8000,  1480.0),   # 7.4★
    (9000,  1560.0),   # 7.8★
    (10000, 1640.0),   # 8.2★
    (11000, 1720.0),   # 8.6★
    (12000, 1800.0),   # 9.0★
    (13000, 1840.0),   # 9.2★
    (14000, 1900.0),   # 9.5★
    (15000, 2000.0),   # 10.0★  (cap)
]


def starting_mu_from_pp(pp: float) -> float:
    """
    Piecewise-linear calibration seed based on osu! pp.
    Returns the target SUM of the four skill components (sum / 200 = starting SR).
    """
    if pp <= 0:
        return _PP_SR_CURVE[0][1]
    if pp >= _PP_SR_CURVE[-1][0]:
        return _PP_SR_CURVE[-1][1]
    for i in range(len(_PP_SR_CURVE) - 1):
        pp0, mu0 = _PP_SR_CURVE[i]
        pp1, mu1 = _PP_SR_CURVE[i + 1]
        if pp0 <= pp <= pp1:
            t = (pp - pp0) / (pp1 - pp0)
            return mu0 + t * (mu1 - mu0)
    return _PP_SR_CURVE[-1][1]


async def get_or_create_rating(user_id: int, mode: str, player_pp: float = 0.0) -> BskRating:
    async with get_db_session() as session:
        stmt = select(BskRating).where(
            BskRating.user_id == user_id,
            BskRating.mode == mode,
        )
        rating = (await session.execute(stmt)).scalar_one_or_none()
        if not rating:
            start_mu = starting_mu_from_pp(player_pp)
            per_comp = start_mu / 4.0
            rating = BskRating(
                user_id=user_id,
                mode=mode,
                mu_aim=per_comp,
                mu_speed=per_comp,
                mu_acc=per_comp,
                mu_cons=per_comp,
                peak_mu=start_mu,
            )
            session.add(rating)
            await session.commit()
            await session.refresh(rating)
        return rating


def _base_k(mode: str) -> float:
    return float(K_RANKED if mode == 'ranked' else K_CASUAL)


def _apply_global_delta(rating: BskRating, delta: float, map_weights: dict) -> None:
    """Distribute the global delta across components by blended map weights."""
    for comp in ('aim', 'speed', 'acc', 'cons'):
        share = _component_share(map_weights.get(comp, 0.25))
        current = getattr(rating, f'mu_{comp}')
        setattr(rating, f'mu_{comp}', _clamp(current + delta * share))


async def update_ratings(
    winner_id: int,
    loser_id: int,
    mode: str,
    map_weights: Optional[dict] = None,
    winner_pp: float = 0.0,
    loser_pp: float = 0.0,
    winner_rounds: int = 1,
    loser_rounds: int = 0,
) -> tuple[BskRating, BskRating]:
    """Update ratings after a duel ends.

    ``map_weights`` should be averaged across the played rounds and sum to 1.
    ``winner_rounds`` / ``loser_rounds`` reflect rounds won — used to compute
    ``result`` so 3:0 differs from 3:2.

    Per-player K is multiplied by ``PLACEMENT_K_MULTIPLIER`` while that player
    has placement matches left, so calibration only accelerates their own
    rating — the calibrated opponent moves at normal pace.

    Returns ``(winner_rating, loser_rating)`` after the in-place update.
    """
    if map_weights is None:
        map_weights = {'aim': 0.25, 'speed': 0.25, 'acc': 0.25, 'cons': 0.25}

    total_rounds = winner_rounds + loser_rounds
    if total_rounds <= 0:
        result = 1.0
    else:
        result = winner_rounds / total_rounds

    async with get_db_session() as session:
        w_stmt = select(BskRating).where(BskRating.user_id == winner_id, BskRating.mode == mode)
        l_stmt = select(BskRating).where(BskRating.user_id == loser_id, BskRating.mode == mode)

        w = (await session.execute(w_stmt)).scalar_one_or_none()
        l = (await session.execute(l_stmt)).scalar_one_or_none()

        if not w:
            w = BskRating(user_id=winner_id, mode=mode)
            if mode == "ranked" and winner_pp > 0:
                start_mu = starting_mu_from_pp(winner_pp)
                per_comp = start_mu / 4.0
                w.mu_aim = w.mu_speed = w.mu_acc = w.mu_cons = per_comp
                w.peak_mu = start_mu
            session.add(w)
        if not l:
            l = BskRating(user_id=loser_id, mode=mode)
            if mode == "ranked" and loser_pp > 0:
                start_mu = starting_mu_from_pp(loser_pp)
                per_comp = start_mu / 4.0
                l.mu_aim = l.mu_speed = l.mu_acc = l.mu_cons = per_comp
                l.peak_mu = start_mu

            session.add(l)

        await session.flush()

        base_k = _base_k(mode)
        w_k = base_k * (PLACEMENT_K_MULTIPLIER if w.placement_matches_left > 0 else 1)
        l_k = base_k * (PLACEMENT_K_MULTIPLIER if l.placement_matches_left > 0 else 1)

        # Aggregate skill — Elo expectation uses the weighted global mu.
        e_w = _expected(w.mu_global, l.mu_global)
        winner_delta = w_k * (result - e_w)
        loser_delta = l_k * ((1.0 - result) - (1.0 - e_w))  # = -l_k * (result - e_w)

        _apply_global_delta(w, winner_delta, map_weights)
        _apply_global_delta(l, loser_delta, map_weights)

        if w.placement_matches_left > 0:
            w.placement_matches_left -= 1
        if l.placement_matches_left > 0:
            l.placement_matches_left -= 1

        w.wins += 1
        l.losses += 1
        now = datetime.now(timezone.utc)
        w.updated_at = now
        l.updated_at = now

        # peak_mu tracks weighted mu_global, matching the value shown on the ladder.
        if w.mu_global > w.peak_mu:
            w.peak_mu = w.mu_global

        await session.commit()
        await session.refresh(w)
        await session.refresh(l)

        return w, l
