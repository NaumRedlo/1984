"""Derive a target's progress on a request from their UserMapAttempt rows.

Nothing is stored — this reads the attempts for (target_user_id, beatmap_id)
logged since the request was accepted and summarizes them: best completion %,
attempt count, and where fails cluster.
"""

from __future__ import annotations

from sqlalchemy import select, func

from db.models.map_attempt import UserMapAttempt


# Fail-clustering buckets over completion %.
_BUCKETS = [(0.0, 25.0), (25.0, 50.0), (50.0, 75.0), (75.0, 100.0)]


def _completion_pct(a: UserMapAttempt) -> float:
    """How far into the map this attempt got, in percent (0–100)."""
    if a.passed:
        return 100.0
    total = a.total_objects or 0
    if total <= 0:
        return 0.0
    hit = (a.count_300 or 0) + (a.count_100 or 0) + (a.count_50 or 0) + (a.count_miss or 0)
    return min(100.0, hit / total * 100.0)


def _bucket_label(pct: float) -> str:
    for lo, hi in _BUCKETS:
        if pct < hi or hi == 100.0 and pct <= hi:
            return f"{int(lo)}-{int(hi)}"
    return f"{int(_BUCKETS[-1][0])}-{int(_BUCKETS[-1][1])}"


async def request_progress(request, session) -> dict:
    """Summarize the target's attempts on the requested map since acceptance.

    Returns: {
        "attempt_count": int,
        "max_completion_pct": float,   # rounded to 0.1
        "passed": bool,                # any attempt already a pass
        "fail_buckets": {label: count},
        "modal_fail_bucket": label | None,   # where fails cluster most
    }
    """
    since = request.responded_at
    stmt = select(UserMapAttempt).where(
        UserMapAttempt.user_id == request.target_user_id,
        UserMapAttempt.beatmap_id == request.beatmap_id,
    )
    if since is not None:
        stmt = stmt.where(
            func.coalesce(UserMapAttempt.played_at, UserMapAttempt.created_at) >= since
        )
    attempts = list((await session.execute(stmt)).scalars().all())

    if not attempts:
        return {
            "attempt_count": 0,
            "max_completion_pct": 0.0,
            "passed": False,
            "fail_buckets": {},
            "modal_fail_bucket": None,
        }

    best = 0.0
    passed_any = False
    fail_buckets: dict[str, int] = {}
    for a in attempts:
        pct = _completion_pct(a)
        best = max(best, pct)
        if a.passed:
            passed_any = True
        else:
            label = _bucket_label(pct)
            fail_buckets[label] = fail_buckets.get(label, 0) + 1

    modal = max(fail_buckets, key=fail_buckets.get) if fail_buckets else None
    return {
        "attempt_count": len(attempts),
        "max_completion_pct": round(best, 1),
        "passed": passed_any,
        "fail_buckets": fail_buckets,
        "modal_fail_bucket": modal,
    }
