"""UR_est — Laplace-smoothed estimator of osu! standard Unstable Rate.

Implements the formula from the HPS Math Manifest (Part I).  Pure math, no I/O.
Used by:
    - HPS bounty payout (Ω module in calculate_hps)
    - BSK duel ML pipeline (per-round UR signal)

Inputs are taken straight from osu! score statistics; the only contextual data
needed is map OD and the active mods (for time/OD scaling).  The estimator
ignores misses on purpose — they are punished separately by C_pen in the HPS
formula and by the duel composite in BSK.

Formula recap (see `/home/naumredlo/HPS Balance`, Part I):
    OD_act = min(10, OD × M_od)            # HR=1.4, EZ=0.5, else 1.0
    W_300  = (80 − 6·OD_act) / M_time      # DT=1.5, HT=0.75, else 1.0
    P_300  = (n_300 + 1) / (N_hits + 2)    # Laplace, miss-free
    Z      = Hastings approximation of the standard normal inverse-quantile
    UR     = 10 × W_300 / Z

Returns None when there is no signal (N_hits = 0) or numerical edge cases make
the result meaningless; callers should treat None as "neutral" (Ω = 1.0).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Union

ModsInput = Union[str, Iterable[str], Iterable[dict], None]


def _normalize_mods(mods: ModsInput) -> set[str]:
    """Accept the assorted mod shapes osu! API returns and yield uppercase strings."""
    if mods is None:
        return set()
    if isinstance(mods, str):
        return {m.strip().upper() for m in mods.replace(",", " ").split() if m.strip()}
    out: set[str] = set()
    for m in mods:
        if isinstance(m, dict):
            acronym = m.get("acronym") or m.get("name")
            if acronym:
                out.add(str(acronym).upper())
        elif isinstance(m, str):
            out.add(m.strip().upper())
    return out


def _hit_window_300(od: float, mods: set[str]) -> float:
    """Return the half-width of the 300 hit window in milliseconds.

    OD scaling: HR multiplies OD by 1.4 (capped at 10), EZ halves it.
    Time scaling: DT shrinks the window by 1/1.5, HT widens it by 1/0.75.
    Returns the unscaled-by-time window divided by the time multiplier — i.e.
    the *effective* window the player has to hit.
    """
    m_od = 1.0
    if "HR" in mods:
        m_od = 1.4
    elif "EZ" in mods:
        m_od = 0.5

    m_time = 1.0
    if "DT" in mods or "NC" in mods:
        m_time = 1.5
    elif "HT" in mods:
        m_time = 0.75

    od_act = min(10.0, od * m_od)
    w_300 = (80.0 - 6.0 * od_act) / m_time
    return max(1.0, w_300)  # floor at 1 ms to avoid divide-by-zero downstream


def _inverse_normal_hastings(q: float) -> float:
    """Hastings rational approximation of |z| from a normal tail probability.

    A&S 26.2.23 / Hastings 1955 is defined for q ∈ (0, 0.5] and returns the
    magnitude of the inverse-quantile (z ≥ 0).  Outside that range we fold
    via `min(q, 1−q)` and rely on the caller to interpret sign — for the UR
    estimator we want |z| anyway, since UR = 10·W/|Z| is sign-invariant.
    """
    if q <= 0.0 or q >= 1.0:
        return 0.0
    p = min(q, 1.0 - q)
    t = math.sqrt(math.log(1.0 / (p * p)))
    num = 2.515517 + 0.802853 * t + 0.010328 * t * t
    den = 1.0 + 1.432788 * t + 0.189269 * t * t + 0.001308 * t * t * t
    return t - num / den


def estimate_ur(
    n_300: int,
    n_100: int,
    n_50: int,
    od: float,
    mods: ModsInput = None,
) -> Optional[float]:
    """Estimate Unstable Rate (ms) from osu!standard hit counts + map OD.

    Args:
        n_300, n_100, n_50: hit counts from the score's statistics block.
            Misses are intentionally not an input — they are not used by the
            tap-timing estimator.
        od: beatmap OverallDifficulty (pre-mods).
        mods: any of the shapes osu! API returns (list of dicts, list of
            strings, or comma/space-separated string).

    Returns:
        UR in milliseconds, or None when no estimate is possible:
            - N_hits = 0 (player did not hit anything in the score),
            - Z degenerates to 0 (numerical edge — shouldn't happen with the
              clamp below, but defended against).

    Notes:
        Laplace smoothing (+1 to n_300, +2 to N) prevents a perfect SS from
        producing P_300 = 1 → q = 0 → ln(∞).  We then clamp q into a safe
        interior of (0,1) so the Hastings polynomial stays well-defined for
        both extreme misses (q → 1) and extreme accuracy (q → 0).
    """
    n_300 = int(n_300 or 0)
    n_100 = int(n_100 or 0)
    n_50 = int(n_50 or 0)
    n_hits = n_300 + n_100 + n_50
    if n_hits == 0:
        return None

    mod_set = _normalize_mods(mods)
    w_300 = _hit_window_300(float(od or 0.0), mod_set)

    p_300 = (n_300 + 1.0) / (n_hits + 2.0)
    eps = 1.0 / (n_hits + 2.0)
    p_300 = max(eps, min(1.0 - eps, p_300))
    q = 1.0 - p_300

    z = _inverse_normal_hastings(q)
    if z <= 0.0:
        return None

    return 10.0 * (w_300 / z)


__all__ = ["estimate_ur"]
