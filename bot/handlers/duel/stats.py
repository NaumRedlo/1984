from aiogram import Router
from aiogram.types import BufferedInputFile, Message

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.duel.common import build_duel_keyboard, get_duel_data
from db.database import get_db_session
from services.image import card_renderer
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_reply_target_user, resolve_registered_user

router = Router(name="duel.stats")


@router.message(TextTriggerFilter("duelstats", "duels"))
async def cmd_duel_stats(message: Message, trigger_args: TriggerArgs = None, osu_api_client=None, tenant_chat_id=None):
    sender_tg = message.from_user.id
    mode = "casual"

    # Precedence: explicit arg > reply-to-user > sender. Replying to someone
    # with bare "duels" shows their duel card.
    query = (trigger_args.args or "").strip() if trigger_args else ""
    target_tg: int | None = None

    if not await ensure_dm_tenant(message, tenant_chat_id):
        return

    if query and osu_api_client:
        async with get_db_session() as session:
            user, _data = await resolve_registered_user(session, osu_api_client, query, tenant_chat_id)
        if not user:
            await message.answer(
                f"Игрок <b>{escape_html(query)}</b> не зарегистрирован в боте — нет дуэлей.",
                parse_mode="HTML",
            )
            return
        target_tg = user.telegram_id
    else:
        async with get_db_session() as session:
            reply_user = await get_reply_target_user(session, message, chat_id=tenant_chat_id)
        if reply_user:
            target_tg = reply_user.telegram_id
        else:
            target_tg = sender_tg

    data = await get_duel_data(target_tg, mode, tenant_chat_id)
    if not data:
        if target_tg == sender_tg:
            await message.answer("Вы не зарегистрированы.")
        else:
            await message.answer("Тот, на кого вы ответили, не зарегистрирован.")
        return
    img_buf = await card_renderer.generate_duel_card_async(data)
    await message.answer_photo(
        BufferedInputFile(img_buf.read(), filename="duel.png"),
        reply_markup=build_duel_keyboard(target_tg, mode),
    )
