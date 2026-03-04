from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select, desc

from db.models.user import User
from db.database import get_db_session
from utils.logger import get_logger

router = Router(name="leaderboard")
logger = get_logger("handlers.leaderboard")

@router.message(Command("leaderboard", "lb", "top"))
async def show_leaderboard(message: types.Message):
    
    async with get_db_session() as session:
        try:
            stmt = (
                select(User)
                .where(User.hps_points > 0)
                .order_by(desc(User.hps_points))
                .limit(10)
            )
            top_users = (await session.execute(stmt)).scalars().all()
            
            if not top_users:
                await message.answer("No users with HP points yet!")
                return
            
            lb_text = "🏆 <b>Hunter Points Leaderboard</b>\n" + "═" * 40 + "\n\n"
            
            for i, user in enumerate(top_users, 1):
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
                
                lb_text += (
                    f"{medal} <b>{user.osu_username}</b>\n"
                    f"   💎 <code>{user.hps_points} HP</code> • "
                    f"🎖️ <code>{user.rank}</code> • "
                    f"📈 <code>{user.player_pp:,} PP</code>\n\n"
                )
            
            await message.answer(lb_text, parse_mode="HTML")
            
        except Exception as e:
            logger.error(f"Error in /leaderboard: {e}", exc_info=True)
            await message.answer("An error occurred while loading leaderboard.")

__all__ = ["router"]
