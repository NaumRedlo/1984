from aiogram import Router, types
from sqlalchemy import select
from datetime import datetime, timezone

from db.models.user import User
from db.database import get_db_session
from config.settings import GROUP_CHAT_ID, ADMIN_IDS
from utils.logger import get_logger
from utils.text_utils import escape_html, format_error
from utils.resolve_user import resolve_osu_user, get_registered_user
from bot.filters import TextTriggerFilter, TriggerArgs

logger = get_logger("handlers.auth")
router = Router(name="auth")


async def _is_group_member(bot, user_id: int) -> bool:
    """Check if user is a member of the restricted group."""
    if not GROUP_CHAT_ID:
        return True
    try:
        member = await bot.get_chat_member(GROUP_CHAT_ID, user_id)
        return member.status not in ("left", "kicked")
    except Exception as e:
        logger.warning(f"Failed to check group membership for {user_id}: {e}")
        return False


@router.message(TextTriggerFilter("register", "reg"))
async def register_user(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    tg_id = message.from_user.id
    raw_username = trigger_args.args

    if not raw_username:
        await message.answer(
            "<b>Укажите ваш osu! никнейм или ID:</b>\n"
            "<code>register Nickname</code> или <code>register id:12345</code>",
            parse_mode="HTML"
        )
        return

    # Check group membership before registration
    if not await _is_group_member(message.bot, tg_id):
        await message.answer(
            format_error("Регистрация доступна только для участников группы."),
            parse_mode="HTML"
        )
        return

    wait_msg = await message.answer(f"Поиск в базе osu!: <b>{escape_html(raw_username)}</b>...", parse_mode="HTML")

    try:
        user_data = await resolve_osu_user(osu_api_client, raw_username)

        if not user_data:
            await wait_msg.edit_text(format_error(f"Пользователь <b>{escape_html(raw_username)}</b> не найден в базе osu!."), parse_mode="HTML")
            return

        osu_id = user_data['id']
        osu_name = user_data['username']

        async with get_db_session() as session:
            # Check if this osu! account is already bound to another TG user
            existing_osu = (await session.execute(
                select(User).where(User.osu_user_id == osu_id, User.telegram_id != tg_id)
            )).scalar_one_or_none()

            if existing_osu:
                await wait_msg.edit_text(
                    format_error(f"Аккаунт osu! <b>{escape_html(osu_name)}</b> уже привязан к другому пользователю."),
                    parse_mode="HTML"
                )
                return

            # Prevent non-admins from re-binding to a different osu! account
            user = await get_registered_user(session, tg_id)
            if user and user.osu_user_id and user.osu_user_id != osu_id:
                if tg_id not in ADMIN_IDS:
                    await wait_msg.edit_text(
                        format_error(
                            f"Ваш профиль уже привязан к <b>{escape_html(user.osu_username)}</b>.\n"
                            "Перепривязка доступна только администраторам."
                        ),
                        parse_mode="HTML"
                    )
                    return

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
                    play_time=int(user_data.get('play_time', 0)),
                    ranked_score=int(user_data.get('ranked_score', 0)),
                    total_hits=int(user_data.get('total_hits', 0)),
                    last_api_update=datetime.now(timezone.utc)
                )
                session.add(user)
                action_text = "зарегистрирован"
            else:
                user.osu_user_id = osu_id
                user.osu_username = osu_name
                action_text = "перепривязан"

            await session.commit()

        await wait_msg.edit_text(
            f"<b>Личность подтверждена!</b>\n\n"
            f"Пользователь <code>{osu_name}</code> {action_text} в системе Project 1984.\n"
            f"Ранг: <code>#{user_data['global_rank']:,}</code>\n"
            f"PP: <code>{user_data['pp']:,}</code>",
            parse_mode="HTML"
        )
        logger.info(f"User {tg_id} successfully {action_text} as {osu_name} (ID: {osu_id})")

    except Exception as e:
        logger.error(f"Failed to register user {tg_id}: {e}", exc_info=True)
        await wait_msg.edit_text(format_error("Системная ошибка при верификации."))

__all__ = ["router"]
