"""Auto-evaluate accepted requests when a target's plays sync.

Called from the refresh pipeline (services/refresh/orchestrator.py) right after
title refresh. Cheap no-op unless the user is the target of an accepted request:
then it pulls their recent plays, indexes them into user_map_attempts, and marks
any request whose conditions are now satisfied as completed (+ notifies).
"""

from __future__ import annotations

from sqlalchemy import select

from db.database import get_db_session
from db.models.map_request import MapRequest, STATUS_ACCEPTED, STATUS_COMPLETED
from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from utils.logger import get_logger
from utils.timeutils import utcnow
from services.requests.conditions import parse, score_meets, play_from_attempt
from services.requests.format import map_label

logger = get_logger("services.requests")


async def evaluate_open_requests(user, session, api_client) -> list:
    """For `user` as a request target: sync recent plays and complete any
    accepted request now satisfied. Returns the list of just-completed requests.
    Best-effort — never raises into the refresh caller."""
    if not getattr(user, "osu_user_id", None):
        return []

    open_reqs = (await session.execute(
        select(MapRequest).where(
            MapRequest.target_user_id == user.id,
            MapRequest.status == STATUS_ACCEPTED,
        )
    )).scalars().all()
    if not open_reqs:
        return []

    # Pull recent plays (passes + fails) and index them, so both completion
    # detection and later progress reads have fresh data.
    try:
        recent = await api_client.get_user_recent_scores(user.osu_user_id, limit=50)
        if recent:
            await api_client.sync_user_map_attempts(user, session, recent)
    except Exception as exc:
        logger.debug(f"request eval: recent sync failed for user_id={user.id}: {exc}")

    completed = []
    for req in open_reqs:
        cond = parse(req.conditions)
        stmt = select(UserMapAttempt).where(
            UserMapAttempt.user_id == req.target_user_id,
            UserMapAttempt.beatmap_id == req.beatmap_id,
        )
        if req.responded_at is not None:
            stmt = stmt.where(
                (UserMapAttempt.played_at.is_(None))
                | (UserMapAttempt.played_at >= req.responded_at)
            )
        attempts = (await session.execute(stmt)).scalars().all()
        for a in attempts:
            if score_meets(cond, play_from_attempt(a)):
                req.status = STATUS_COMPLETED
                req.completed_at = utcnow()
                req.completing_score_id = a.score_id
                completed.append(req)
                break

    if not completed:
        return []

    await session.commit()

    # Notify outside the refresh's transactional path (best-effort).
    for req in completed:
        try:
            await _notify(req)
        except Exception as exc:
            logger.debug(f"request completion notify failed for req={req.id}: {exc}")
    return completed


async def _notify(req: MapRequest) -> None:
    """Load the names for a completed request and fire the notification."""
    from services.requests.notify import notify_completed
    from utils.language import get_language
    async with get_db_session() as s:
        sender = await s.get(User, req.sender_user_id)
        target = await s.get(User, req.target_user_id)
    if not (sender and target):
        return
    lang = (await get_language(target.telegram_id)).lower()
    await notify_completed(
        chat_id=req.tenant_chat_id,
        sender_name=sender.osu_username,
        target_tg_id=target.telegram_id,
        target_name=target.osu_username,
        map_label=map_label(req.artist, req.title, req.version, req.beatmap_id),
        beatmap_id=req.beatmap_id, beatmapset_id=req.beatmapset_id,
        lang=lang,
    )
