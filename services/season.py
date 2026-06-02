"""Season management service."""
from datetime import datetime, timezone

from sqlalchemy import select, delete as sa_delete, func as sa_func

from db.database import get_db_session
from db.models.duel_rating import DuelRating
from db.models.season import Season
from db.models.season_snapshot import SeasonSnapshot
from db.models.user import User
from utils.hp_calculator import (
    get_division_for_hp,
    get_rank_for_hp,
    SEASON_BONUS_HPS,
)
from utils.logger import get_logger

logger = get_logger("services.season")


async def get_current_season(session) -> Season | None:
    return (await session.execute(
        select(Season).where(Season.is_active == 1)
    )).scalar_one_or_none()


async def start_new_season() -> Season:
    from services.duel.rating import starting_mu_from_pp

    async with get_db_session() as session:
        old_season = await get_current_season(session)
        old_number = old_season.number if old_season else 0
        now = datetime.now(timezone.utc)

        users = (await session.execute(select(User))).scalars().all()

        # Fetch all DUEL ranked ratings for conservative snapshot
        duel_ratings = (await session.execute(
            select(DuelRating).where(DuelRating.mode == 'ranked')
        )).scalars().all()
        duel_by_user: dict[int, DuelRating] = {r.user_id: r for r in duel_ratings}

        new_season = Season(
            number=old_number + 1,
            started_at=now,
            is_active=1,
        )
        session.add(new_season)
        await session.flush()  # get new_season.id

        updated = 0
        for u in users:
            hps_div = get_division_for_hp(u.hps_points or 0)
            bonus = SEASON_BONUS_HPS.get(hps_div, 0)

            duel_r = duel_by_user.get(u.id)
            duel_cons = float(duel_r.conservative) if duel_r else None
            from utils.hp_calculator import get_division_for_conservative
            duel_div = get_division_for_conservative(duel_cons) if duel_cons is not None else None

            snapshot = SeasonSnapshot(
                season_id=new_season.id,
                user_id=u.id,
                hps_points=u.hps_points or 0,
                hps_division=hps_div,
                duel_conservative=duel_cons,
                duel_division=duel_div,
            )
            session.add(snapshot)

            u.season_bonus_hps = bonus
            u.hps_points = bonus
            u.rank = get_rank_for_hp(bonus)
            updated += 1

        # Soft-reset DUEL ratings: reseed mu from pp, reset sigma to full
        # uncertainty so the new season re-calibrates.
        from services.duel.rating import DUEL_TS_SIGMA0, PLACEMENT_MATCHES
        all_duel = (await session.execute(select(DuelRating))).scalars().all()
        user_pp: dict[int, float] = {u.id: float(u.player_pp or 0) for u in users}
        for r in all_duel:
            start_mu = starting_mu_from_pp(user_pp.get(r.user_id, 0.0))
            r.mu = start_mu
            r.sigma = DUEL_TS_SIGMA0
            r.placement_matches_left = PLACEMENT_MATCHES
            r.peak_mu = start_mu
            r.season_id = new_season.id
            r.updated_at = now

        # Close old season
        if old_season:
            old_season.ended_at = now
            old_season.is_active = 0

        await session.commit()
        await session.refresh(new_season)

    logger.info(f"start_new_season: season {new_season.number} started, {updated} users reset")
    return new_season


async def wipe_all_hp() -> int:
    """Zero every user's HPS points, season bonus and rank.

    Affects only the HPS layer — DUEL ratings and snapshots are left intact.
    Returns the number of users reset.
    """
    rank0 = get_rank_for_hp(0)
    async with get_db_session() as session:
        users = (await session.execute(select(User))).scalars().all()
        for u in users:
            u.hps_points = 0
            u.season_bonus_hps = 0
            u.rank = rank0
        await session.commit()
    logger.warning(f"wipe_all_hp: reset HPS for {len(users)} users")
    return len(users)


async def list_all_seasons() -> list[Season]:
    """All seasons, newest first."""
    async with get_db_session() as session:
        return (await session.execute(
            select(Season).order_by(Season.number.desc())
        )).scalars().all()


async def void_season(number: int) -> dict:
    """Annul (delete) a season record and its snapshots from the DB.

    If the voided season was the active one, the highest-numbered remaining
    season is reactivated.  User HPS points and DUEL ratings are **not**
    modified — this only removes the season bookkeeping (use ``wipe_all_hp`` /
    ``start_new_season`` to change player stats).  Returns a result dict.
    """
    async with get_db_session() as session:
        season = (await session.execute(
            select(Season).where(Season.number == number)
        )).scalar_one_or_none()
        if not season:
            return {"ok": False, "reason": "not_found"}

        was_active = bool(season.is_active)
        snap_count = (await session.execute(
            select(sa_func.count()).select_from(SeasonSnapshot)
            .where(SeasonSnapshot.season_id == season.id)
        )).scalar() or 0

        await session.execute(
            sa_delete(SeasonSnapshot).where(SeasonSnapshot.season_id == season.id)
        )
        await session.delete(season)

        reactivated = None
        if was_active:
            prev = (await session.execute(
                select(Season).where(Season.number != number)
                .order_by(Season.number.desc())
            )).scalars().first()
            if prev:
                prev.is_active = 1
                prev.ended_at = None
                reactivated = prev.number

        await session.commit()

    logger.warning(
        f"void_season: removed season {number} ({snap_count} snapshots), "
        f"was_active={was_active}, reactivated={reactivated}"
    )
    return {"ok": True, "snapshots": snap_count, "was_active": was_active, "reactivated": reactivated}
