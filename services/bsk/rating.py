"""
BSK rating update logic.
4 skill components: aim, speed, acc, cons.
mu_global = 0.30*aim + 0.30*speed + 0.25*acc + 0.15*cons
K=8 casual, K=16 ranked. Placement matches use K*2.
Floor/ceiling: each component in [0, 1000].
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from db.database import get_db_session
from db.models.bsk_rating import BskRating

COMPONENT_FLOOR = 0.0
COMPONENT_CEILING = 1000.0
K_CASUAL = 8
K_RANKED = 16
SIGMA_DECAY = 0.95
SIGMA_FLOOR = 20.0
C = 400.0  # scale constant for expected score


def _k_factor(mode: str, placement: bool) -> float:
    base = K_RANKED if mode == 'ranked' else K_CASUAL
    return float(base * 2 if placement else base)


def _expected(mu_a: float, mu_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((mu_b - mu_a) / C))


def _clamp(value: float) -> float:
    return max(COMPONENT_FLOOR, min(COMPONENT_CEILING, value))


def _update_component(
    mu_a: float, mu_b: float,
    sigma_a: float, sigma_b: float,
    k: float, result: float,
    w: float,
) -> tuple[float, float, float, float]:
    """Returns (new_mu_a, new_mu_b, new_sigma_a, new_sigma_b)."""
    e = _expected(mu_a, mu_b)
    delta = k * (result - e) * w
    new_mu_a = _clamp(mu_a + delta)
    new_mu_b = _clamp(mu_b - delta)
    new_sigma_a = max(sigma_a * SIGMA_DECAY, SIGMA_FLOOR)
    new_sigma_b = max(sigma_b * SIGMA_DECAY, SIGMA_FLOOR)
    return new_mu_a, new_mu_b, new_sigma_a, new_sigma_b


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
    map_weights: Optional[dict] = None,
) -> tuple[BskRating, BskRating]:
    """
    Update ratings after a duel. map_weights = {aim, speed, acc, cons} summing to 1.
    Defaults to equal weights if not provided.
    Returns (winner_rating, loser_rating).
    """
    if map_weights is None:
        map_weights = {'aim': 0.25, 'speed': 0.25, 'acc': 0.25, 'cons': 0.25}

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

        placement = w.placement_matches_left > 0 or l.placement_matches_left > 0
        k = _k_factor(mode, placement)

        components = [
            ('aim',   map_weights.get('aim',   0.25)),
            ('speed', map_weights.get('speed', 0.25)),
            ('acc',   map_weights.get('acc',   0.25)),
            ('cons',  map_weights.get('cons',  0.25)),
        ]

        for comp, weight in components:
            mu_w = getattr(w, f'mu_{comp}')
            mu_l = getattr(l, f'mu_{comp}')
            sig_w = getattr(w, f'sigma_{comp}')
            sig_l = getattr(l, f'sigma_{comp}')

            new_mu_w, new_mu_l, new_sig_w, new_sig_l = _update_component(
                mu_w, mu_l, sig_w, sig_l, k, result=1.0, w=weight
            )

            setattr(w, f'mu_{comp}', new_mu_w)
            setattr(l, f'mu_{comp}', new_mu_l)
            setattr(w, f'sigma_{comp}', new_sig_w)
            setattr(l, f'sigma_{comp}', new_sig_l)

        if w.placement_matches_left > 0:
            w.placement_matches_left -= 1
        if l.placement_matches_left > 0:
            l.placement_matches_left -= 1

        w.wins += 1
        l.losses += 1
        now = datetime.now(timezone.utc)
        w.updated_at = now
        l.updated_at = now

        await session.commit()
        await session.refresh(w)
        await session.refresh(l)

        return w, l
