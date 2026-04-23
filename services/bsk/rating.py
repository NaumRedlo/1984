"""
BSK rating update logic.
Uses Elo-like mu update with sigma as confidence factor.
K=8 for casual, K=16 for ranked. Placement matches use K*2/sigma*2.
Floor/ceiling: mu in [0, 3000].
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from db.database import get_db_session
from db.models.bsk_rating import BskRating

MU_FLOOR = 0.0
MU_CEILING = 3000.0
K_CASUAL = 8
K_RANKED = 16
SIGMA_DECAY = 0.95  # sigma shrinks each match toward confidence
SIGMA_FLOOR = 50.0
PLACEMENT_MATCHES = 10


def _k_factor(mode: str, placement: bool, sigma: float) -> float:
    base = K_RANKED if mode == 'ranked' else K_CASUAL
    if placement:
        return base * 2 * (sigma / 200.0)
    return float(base)


def _expected(mu_a: float, mu_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((mu_b - mu_a) / 400.0))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


async def get_or_create_rating(user_id: int, mode: str) -> BskRating:
    async with get_db_session() as session:
        stmt = select(BskRating).where(
            BskRating.user_id == user_id,
            BskRating.mode == mode,
        )
        rating = (await session.execute(stmt)).scalar_one_or_none()
        if not rating:
            rating = BskRating(user_id=user_id, mode=mode)
            session.add(rating)
            await session.commit()
            await session.refresh(rating)
        return rating


async def update_ratings(
    winner_id: int,
    loser_id: int,
    mode: str,
    winner_mechanical: Optional[float] = None,
    winner_precision: Optional[float] = None,
    loser_mechanical: Optional[float] = None,
    loser_precision: Optional[float] = None,
) -> tuple[BskRating, BskRating]:
    """Update mu/sigma for winner and loser. Returns (winner_rating, loser_rating)."""
    async with get_db_session() as session:
        w_stmt = select(BskRating).where(BskRating.user_id == winner_id, BskRating.mode == mode)
        l_stmt = select(BskRating).where(BskRating.user_id == loser_id, BskRating.mode == mode)

        w = (await session.execute(w_stmt)).scalar_one_or_none()
        l = (await session.execute(l_stmt)).scalar_one_or_none()

        if not w:
            w = BskRating(user_id=winner_id, mode=mode)
            session.add(w)
        if not l:
            l = BskRating(user_id=loser_id, mode=mode)
            session.add(l)

        await session.flush()

        w_placement = w.placement_matches_left > 0
        l_placement = l.placement_matches_left > 0

        k_w = _k_factor(mode, w_placement, w.sigma)
        k_l = _k_factor(mode, l_placement, l.sigma)

        e_w = _expected(w.mu, l.mu)
        e_l = _expected(l.mu, w.mu)

        w.mu = _clamp(w.mu + k_w * (1.0 - e_w), MU_FLOOR, MU_CEILING)
        l.mu = _clamp(l.mu + k_l * (0.0 - e_l), MU_FLOOR, MU_CEILING)

        w.sigma = max(w.sigma * SIGMA_DECAY, SIGMA_FLOOR)
        l.sigma = max(l.sigma * SIGMA_DECAY, SIGMA_FLOOR)

        if w_placement:
            w.placement_matches_left -= 1
        if l_placement:
            l.placement_matches_left -= 1

        if winner_mechanical is not None:
            w.mechanical = winner_mechanical
        if winner_precision is not None:
            w.precision = winner_precision
        if loser_mechanical is not None:
            l.mechanical = loser_mechanical
        if loser_precision is not None:
            l.precision = loser_precision

        w.wins += 1
        l.losses += 1
        w.updated_at = datetime.now(timezone.utc)
        l.updated_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(w)
        await session.refresh(l)

        return w, l
