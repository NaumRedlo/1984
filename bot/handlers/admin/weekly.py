from aiogram import Router, types

from bot.filters import TextTriggerFilter
from db.database import get_db_session
from utils.admin_check import AdminFilter
from utils.formatting.text import format_error, format_success
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_weekly")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


async def _save_chat_and_thread(
    chat_key: str, thread_key: str, message: types.Message,
) -> tuple[int, int | None]:
    """Persist the current chat id and the forum topic the command was run in.

    message_thread_id is None in the General topic / a non-forum chat; we
    store '' in that case so re-running the command in General resets any
    previously saved topic.
    """
    from db.models.bot_settings import BotSettings
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    chat_id = message.chat.id
    thread_id = message.message_thread_id
    pairs = (
        (chat_key, str(chat_id)),
        (thread_key, "" if thread_id is None else str(thread_id)),
    )
    async with get_db_session() as session:
        for key, value in pairs:
            stmt = sqlite_insert(BotSettings).values(key=key, value=value)
            stmt = stmt.on_conflict_do_update(
                index_elements=["key"], set_={"value": value},
            )
            await session.execute(stmt)
        await session.commit()
    return chat_id, thread_id


def _topic_suffix(thread_id: int | None) -> str:
    return f", топик <code>{thread_id}</code>" if thread_id is not None else ""


@router.message(TextTriggerFilter("setweeklychat", "swc"))
async def cmd_set_weekly_chat(message: types.Message):
    chat_id, thread_id = await _save_chat_and_thread(
        "weekly_chat_id", "weekly_thread_id", message,
    )
    await message.answer(
        format_success(
            f"Еженедельная рассылка баунти настроена на этот чат "
            f"(<code>{chat_id}</code>{_topic_suffix(thread_id)})."
        ),
        parse_mode="HTML",
    )


@router.message(TextTriggerFilter("setbsknotifychat", "sbnc"))
async def cmd_set_bsk_notify_chat(message: types.Message):
    from db.models.bot_settings import BotSettings
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    chat_id = str(message.chat.id)
    async with get_db_session() as session:
        stmt = sqlite_insert(BotSettings).values(key="bsk_notify_chat_id", value=chat_id)
        stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": chat_id})
        await session.execute(stmt)
        await session.commit()
    await message.answer(
        format_success(f"BSK-уведомления о смене дивизиона настроены на этот чат ({chat_id})."),
        parse_mode="HTML",
    )


@router.message(TextTriggerFilter("setbountychat", "sbc"))
async def cmd_set_bounty_notify_chat(message: types.Message):
    chat_id, thread_id = await _save_chat_and_thread(
        "bounty_notify_chat_id", "bounty_notify_thread_id", message,
    )
    await message.answer(
        format_success(
            f"Уведомления о баунти настроены на этот чат "
            f"(<code>{chat_id}</code>{_topic_suffix(thread_id)})."
        ),
        parse_mode="HTML",
    )


@router.message(TextTriggerFilter("sendweekly", "sw"))
async def cmd_send_weekly(message: types.Message):
    from tasks.bounty_weekly import send_weekly_digest, _get_weekly_target

    chat_id, thread_id = await _get_weekly_target()
    if not chat_id:
        await message.answer(format_error("Чат для рассылки не настроен. Используй setweeklychat."), parse_mode="HTML")
        return

    wait = await message.answer("Генерирую дайджест…")
    try:
        await send_weekly_digest(message.bot, chat_id, thread_id)
        await wait.edit_text(format_success(f"Дайджест отправлен в чат {chat_id}."), parse_mode="HTML")
    except Exception as e:
        await wait.edit_text(format_error(f"Ошибка: {e}"), parse_mode="HTML")
