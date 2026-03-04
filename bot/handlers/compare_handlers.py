from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select

from db.models.user import User
from db.database import get_db_session
from utils.logger import get_logger
from utils.text_utils import escape_html, format_error

router = Router(name="compare")
logger = get_logger("handlers.compare")

@router.message(Command("compare"))
async def compare_users(message: types.Message, osu_api_client):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Usage: <code>/compare &lt;username or id&gt;</code>\n"
            "Examples: <code>/compare Cookiezi</code> or <code>/compare id:12345</code>",
            parse_mode="HTML"
        )
        return

    target_username = args[1].strip()
    tg_id = message.from_user.id

    async with get_db_session() as session:
        try:
            stmt = select(User).where(User.telegram_id == tg_id)
            user1 = (await session.execute(stmt)).scalar_one_or_none()

            if not user1:
                await message.answer(format_error("You need to register first! Use /register"), parse_mode="HTML")
                return

            wait_msg = await message.answer("Fetching data...")

            if target_username.lower().startswith("id:"):
                search_query = target_username[3:].strip()
                target_data = await osu_api_client.get_user_data(int(search_query))
            else:
                target_data = await osu_api_client.get_user_data(target_username)
            
            if not target_data:
                await wait_msg.edit_text(f"User <b>{escape_html(target_username)}</b> not found!", parse_mode="HTML")
                return
            
            pp_diff = user1.player_pp - target_data['pp']
            rank_diff = target_data['global_rank'] - user1.global_rank if target_data['global_rank'] and user1.global_rank else 0
            acc_diff = user1.accuracy - target_data['accuracy']
            
            compare_text = (
                f"📊 <b>Comparison: {user1.osu_username} vs {target_data['username']}</b>\n"
                f"{'═' * 40}\n\n"
                f"📈 <b>PP:</b>\n"
                f"  • You: <code>{user1.player_pp:,}</code> ({_format_diff(pp_diff)} PP)\n"
                f"  • Them: <code>{target_data['pp']:,}</code>\n\n"
                f"🌍 <b>Global Rank:</b>\n"
                f"  • You: <code>#{user1.global_rank:,}</code> ({_format_diff(rank_diff, reverse=True)} places)\n"
                f"  • Them: <code>#{target_data['global_rank']:,}</code>\n\n"
                f"🎯 <b>Accuracy:</b>\n"
                f"  • You: <code>{user1.accuracy:.2f}%</code> ({_format_diff(acc_diff, suffix='%')})\n"
                f"  • Them: <code>{target_data['accuracy']:.2f}%</code>\n\n"
                f"🎮 <b>Playcount:</b>\n"
                f"  • You: <code>{user1.play_count:,}</code>\n"
                f"  • Them: <code>{target_data['play_count']:,}</code>"
            )
            
            await wait_msg.edit_text(compare_text, parse_mode="HTML")
            
        except Exception as e:
            logger.error(f"Error in /compare: {e}", exc_info=True)
            await message.answer(format_error("An error occurred during comparison."), parse_mode="HTML")


def _format_diff(value: float, reverse: bool = False, suffix: str = '') -> str:
    if value == 0:
        return "±0" + suffix
    
    is_positive = value > 0 if not reverse else value < 0
    symbol = "+" if value > 0 else ""
    emoji = "🟢" if is_positive else "🔴"
    
    return f"{emoji} {symbol}{value:,.2f}{suffix}"

__all__ = ["router"]
