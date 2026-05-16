"""Season management service."""
from datetime import datetime, timezone

from sqlalchemy import select

from db.database import get_db_session
from db.models.bsk_rating import BskRating
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
    from services.bsk.rating import starting_mu_from_pp

    async with get_db_session() as session:
        old_season = await get_current_season(session)
        old_number = old_season.number if old_season else 0
        now = datetime.now(timezone.utc)

        users = (await session.execute(select(User))).scalars().all()

        # Fetch all BSK ranked ratings for conservative snapshot
        bsk_ratings = (await session.execute(
            select(BskRating).where(BskRating.mode == 'ranked')
        )).scalars().all()
        bsk_by_user: dict[int, BskRating] = {r.user_id: r for r in bsk_ratings}

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

            bsk_r = bsk_by_user.get(u.id)
            bsk_cons = float(bsk_r.conservative) if bsk_r else None
            from utils.hp_calculator import get_division_for_conservative
            bsk_div = get_division_for_conservative(bsk_cons) if bsk_cons is not None else None

            snapshot = SeasonSnapshot(
                season_id=new_season.id,
                user_id=u.id,
                hps_points=u.hps_points or 0,
                hps_division=hps_div,
                bsk_conservative=bsk_cons,
                bsk_division=bsk_div,
            )
            session.add(snapshot)

            u.season_bonus_hps = bonus
            u.hps_points = bonus
            u.rank = get_rank_for_hp(bonus)
            updated += 1

        # Soft-reset BSK ratings
        all_bsk = (await session.execute(select(BskRating))).scalars().all()
        user_pp: dict[int, float] = {u.id: float(u.player_pp or 0) for u in users}
        for r in all_bsk:
            start_mu = starting_mu_from_pp(user_pp.get(r.user_id, 0.0))
            per_comp = start_mu / 4.0
            r.mu_aim = r.mu_speed = r.mu_acc = r.mu_cons = per_comp
            r.sigma_aim = r.sigma_speed = r.sigma_acc = r.sigma_cons = 100.0
            r.placement_matches_left = 10
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
