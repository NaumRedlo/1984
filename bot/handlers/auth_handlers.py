from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy import select
from datetime import datetime, timezone

from db.models.user import User
from db.database import get_db_session
from utils.logger import get_logger
from utils.text_utils import escape_html, format_error

logger = get_logger("handlers.auth")
router = Router(name="auth") 

@router.message(Command("register"))
async def register_user(message: types.Message, command: CommandObject, osu_api_client):
    tg_id = message.from_user.id
    raw_username = command.args

    if not raw_username:
        await message.answer(
            "<b>Please specify your osu! nickname or ID:</b>\n"
            "<code>/register Nickname</code> or <code>/register id:12345</code>",
            parse_mode="HTML"
        )
        return

    wait_msg = await message.answer(f"Accessing osu! database for: <b>{escape_html(raw_username)}</b>...", parse_mode="HTML")

    try:
        search_query = raw_username.strip()
        force_id = False

        if search_query.lower().startswith("id:"):
            search_query = search_query[3:].strip()
            force_id = True

        if force_id:
            user_data = await osu_api_client.get_user_data(int(search_query))
        else:
            user_data = await osu_api_client.get_user_data(search_query)
        
        if not user_data:
            await wait_msg.edit_text(format_error(f"User <b>{escape_html(raw_username)}</b> not found in osu! database."), parse_mode="HTML")
            return

        osu_id = user_data['id']
        osu_name = user_data['username']

        async with get_db_session() as session:
            existing_osu = (await session.execute(
                select(User).where(User.osu_user_id == osu_id, User.telegram_id != tg_id)
            )).scalar_one_or_none()

            if existing_osu:
                await wait_msg.edit_text(
                    format_error(f"osu! account <b>{escape_html(osu_name)}</b> is already linked to another user."),
                    parse_mode="HTML"
                )
                return

            stmt = select(User).where(User.telegram_id == tg_id)
            user = (await session.execute(stmt)).scalar_one_or_none()

            if not user:
                user = User(
                    telegram_id=tg_id,
                    osu_user_id=osu_id,
                    osu_username=osu_name,
                    player_pp=int(user_data['pp']),
                    global_rank=user_data['global_rank'] or 0,
                    country=user_data['country_code'],
                    accuracy=round(user_data['accuracy'], 2),
                    play_count=user_data['play_count'],
                    last_api_update=datetime.now(timezone.utc)
                )
                session.add(user)
                action_text = "registered"
            else:
                user.osu_user_id = osu_id
                user.osu_username = osu_name
                action_text = "re-linked"

            await session.commit()

        await wait_msg.edit_text(
            f"<b>Identity Verified!</b>\n\n"
            f"User <code>{osu_name}</code> has been {action_text} in Project 1984 protocols.\n"
            f"Rank: <code>#{user_data['global_rank']:,}</code>\n"
            f"PP: <code>{user_data['pp']:,}</code>",
            parse_mode="HTML"
        )
        logger.info(f"User {tg_id} successfully {action_text} as {osu_name} (ID: {osu_id})")

    except Exception as e:
        logger.error(f"Failed to register user {tg_id}: {e}", exc_info=True)
        await wait_msg.edit_text(format_error("System error during identity verification."))

__all__ = ["router"]
