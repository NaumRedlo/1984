"""
BSK rating update logic.

Four skill components: aim, speed, acc, cons. Global rating exposed via
``BskRating.mu_global`` (weighted: 0.30·aim + 0.30·speed + 0.25·acc + 0.15·cons).

Ratings are updated **once per duel**, not per round. ``result`` reflects the
score share, so a 3:0 sweep moves the rating more than a 3:2 nail-biter.

K-factors (per duel):
    casual: K = 24
    ranked: K = 32
A player still in placement (``placement_matches_left > 0``) gets their delta
multiplied by ``PLACEMENT_K_MULTIPLIER`` (2×) **only when that delta is positive**
(i.e. they won or upset above expectation). Losses are not amplified during
calibration — a newcomer should not bleed rating from honest losses to much
stronger opponents. Multiplier applies only to that player — the calibrated
opponent is unaffected, breaking strict zero-sum during calibration on purpose.

Skill-gap dampening:
    When the higher-rated player wins as expected, both deltas are scaled by
    ``exp(-(gap / GAP_DAMPEN_SCALE)^2)`` where ``gap = |mu_a_global − mu_b_global|``.
    Result: a strong favourite squashing a much weaker opponent gains very
    little, and the weaker opponent loses very little. Upsets (lower-rated
    player wins) bypass the dampener, so a newcomer is rewarded fully for a
    surprise win and the favourite drops at full Elo magnitude.

Component dispatch:
    1. Compute a single global delta = K · (result - expected_a).
    2. Each component receives the full delta (so ``mu_global`` moves by
       exactly ``delta``), with a tilt proportional to ``map_weights`` so
       components emphasised by the map move a little faster while still
       nudging the others. The baseline keeps a specialty map from freezing
       three skills entirely.

Component values are clamped to ``[COMPONENT_FLOOR, COMPONENT_CEILING]``.
"""

import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from db.database import get_db_session
from db.models.bsk_rating import BskRating
from utils.hp_calculator import get_division_for_conservative

COMPONENT_FLOOR = 0.0
COMPONENT_CEILING = 1000.0
K_CASUAL = 24
K_RANKED = 32
C = 400.0  # scale constant for expected score

PLACEMENT_K_MULTIPLIER = 2
# Per-component tilt around the full delta: map-emphasised components get up to
# (1 + WEIGHT_TILT) × delta, others get (1 - WEIGHT_TILT) × delta. Average across
# components stays at delta, so mu_global moves by exactly the Elo delta.
WEIGHT_TILT = 0.25
# Skill-gap dampening: when the favourite wins, both deltas are scaled by a
# Gaussian of the global-mu gap. Upsets are unaffected (full Elo magnitude).
GAP_DAMPEN_SCALE = 200.0


def _expected(mu_a: float, mu_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((mu_b - mu_a) / C))


def _clamp(value: float) -> float:
    return max(COMPONENT_FLOOR, min(COMPONENT_CEILING, value))


def _component_share(weight: float) -> float:
    """Per-component multiplier on the global delta.

    ``weight`` is a normalized map weight in [0, 1] that sums to 1.0 across the
    four components. We map it to the range [1 - TILT, 1 + TILT] centred on
    1.0 by treating 0.25 as the neutral point:

        share = 1.0 + WEIGHT_TILT · (4·weight − 1)

    Uniform map (each weight = 0.25) → share = 1.0 for every component.
    Pure aim map (aim = 1.0, others = 0.0) with TILT=0.5 →
        aim   = 1.0 + 0.5·(4·1.0 − 1) = 2.5
        other = 1.0 + 0.5·(4·0.0 − 1) = 0.5
    Average across components stays 1.0, so ``mu_global`` moves by exactly the
    Elo delta regardless of map type.
    """
    return 1.0 + WEIGHT_TILT * (4.0 * weight - 1.0)


# ---------------------------------------------------------------------------
# Piecewise-linear calibration curve  (pp → target component SUM)
# sum / 200 = starting SR,  per_comp = sum / 4
# ---------------------------------------------------------------------------
_PP_SR_CURVE: list[tuple[float, float]] = [
    (0,      600.0),   # 3.0★  — new-player floor; 0pp still implies some familiarity
    (1000,  1000.0),   # 5.0★
    (3000,  1300.0),   # 6.5★
    (5000,  1500.0),   # 7.5★
    (7000,  1640.0),   # 8.2★
    (9000,  1760.0),   # 8.8★
    (11000, 1860.0),   # 9.3★
    (13000, 1920.0),   # 9.6★
    (15000, 2000.0),   # 10.0★  (cap)
]


def starting_mu_from_pp(pp: float) -> float:
    """Piecewise-linear calibration seed based on osu! pp.

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


def _starting_mu_from_bsk_user(
    aim: float, speed: float, acc: float, cons: float
) -> dict[str, float]:
    """Seed per-component mu from HPS BSK_user skill axes (each on the 0-10 scale).

    BSK_user axis × 50 = starting mu_component (so 5★ player → mu=250 per axis,
    sum=1000, sum/200=5★ target SR).  Preferred over the pp curve when available
    because it reflects actual per-axis proficiency rather than a flat seed.
    """
    return {
        'aim':   _clamp(aim   * 50),
        'speed': _clamp(speed * 50),
        'acc':   _clamp(acc   * 50),
        'cons':  _clamp(cons  * 50),
    }


async def get_or_create_rating(
    user_id: int,
    mode: str,
    player_pp: float = 0.0,
    bsk_user_aim: Optional[float] = None,
    bsk_user_speed: Optional[float] = None,
    bsk_user_acc: Optional[float] = None,
    bsk_user_cons: Optional[float] = None,
) -> BskRating:
    async with get_db_session() as session:
        stmt = select(BskRating).where(
            BskRating.user_id == user_id,
            BskRating.mode == mode,
        )
        rating = (await session.execute(stmt)).scalar_one_or_none()
        if not rating:
            has_bsk = all(v is not None for v in (bsk_user_aim, bsk_user_speed, bsk_user_acc, bsk_user_cons))
            if has_bsk:
                comp = _starting_mu_from_bsk_user(bsk_user_aim, bsk_user_speed, bsk_user_acc, bsk_user_cons)
                start_mu = sum(comp.values())
            else:
                start_mu = starting_mu_from_pp(player_pp)
                per = start_mu / 4.0
                comp = {'aim': per, 'speed': per, 'acc': per, 'cons': per}
            rating = BskRating(
                user_id=user_id,
                mode=mode,
                mu_aim=comp['aim'],
                mu_speed=comp['speed'],
                mu_acc=comp['acc'],
                mu_cons=comp['cons'],
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
) -> tuple[BskRating, BskRating, str, str, str, str]:
    """Update ratings after a duel ends.

    ``map_weights`` should be averaged across the played rounds and sum to 1.
    ``winner_rounds`` / ``loser_rounds`` reflect rounds won — used to compute
    ``result`` so 3:0 differs from 3:2.

    Per-player K is multiplied by ``PLACEMENT_K_MULTIPLIER`` while that player
    has placement matches left, so calibration only accelerates their own
    rating — the calibrated opponent moves at normal pace.

    Returns ``(winner_rating, loser_rating, w_old_div, w_new_div, l_old_div, l_new_div)``
    after the in-place update.
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

        # Capture divisions before update
        w_old_div = get_division_for_conservative(w.conservative)
        l_old_div = get_division_for_conservative(l.conservative)

        base_k = _base_k(mode)

        # Aggregate skill — Elo expectation uses the weighted global mu.
        e_w = _expected(w.mu_global, l.mu_global)
        raw_winner_delta = base_k * (result - e_w)
        raw_loser_delta = -raw_winner_delta

        # Gap dampening: when the favourite wins as expected, shrink both
        # deltas. Upsets (winner was the underdog) bypass dampening.
        favourite_won = w.mu_global >= l.mu_global
        if favourite_won:
            gap = abs(w.mu_global - l.mu_global)
            dampen = math.exp(-((gap / GAP_DAMPEN_SCALE) ** 2))
            raw_winner_delta *= dampen
            raw_loser_delta *= dampen

        # Placement multiplier: only amplify a player's *gain*. Losses during
        # placement are not doubled — a newcomer shouldn't haemorrhage rating
        # for an expected defeat against a much stronger opponent.
        winner_delta = raw_winner_delta * (PLACEMENT_K_MULTIPLIER if w.placement_matches_left > 0 else 1)
        loser_delta = raw_loser_delta  # always negative; no placement amplification

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

        # peak_mu tracks the sum of all 4 components (matches BSK POINTS display).
        w_mu_sum = w.mu_aim + w.mu_speed + w.mu_acc + w.mu_cons
        if w_mu_sum > w.peak_mu:
            w.peak_mu = w_mu_sum

        await session.commit()
        await session.refresh(w)
        await session.refresh(l)

        w_new_div = get_division_for_conservative(w.conservative)
        l_new_div = get_division_for_conservative(l.conservative)

        return w, l, w_old_div, w_new_div, l_old_div, l_new_div
