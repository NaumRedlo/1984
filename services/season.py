"""Season management service."""
from datetime import datetime, timezone

from sqlalchemy import select

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
