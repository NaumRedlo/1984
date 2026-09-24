from aiogram import Router, types
from sqlalchemy import func, select

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.dm_tenant import ensure_dm_tenant
from db.database import get_db_session
from db.models.user import User
from utils.formatting.text import escape_html, format_error
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.osu.resolve_user import resolve_osu_user

logger = get_logger("handlers.find")
router = Router(name="find")


async def _name_in_chat(bot, chat_id: int, telegram_id: int) -> str | None:
    """How the member is called in the chat, or None when they are no longer in it."""
    try:
        member = await bot.get_chat_member(chat_id, telegram_id)
    except Exception:
        return None
    if member.status in ("left", "kicked"):
        return None
    return member.user.full_name or member.user.username or str(telegram_id)


@router.message(TextTriggerFilter("find"))
async def cmd_find(message: types.Message, trigger_args: TriggerArgs, osu_api_client=None, tenant_chat_id=None):
    lang = (await get_language(message.from_user.id)).lower()
    query = (trigger_args.args or "").strip().lstrip("@")
    if not query:
        await message.answer(t("find.usage", lang), parse_mode="HTML")
        return
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return

    # osu! knows renamed players by their old names too; the chat only knows the name they linked
    osu_id, shown = None, query
    if osu_api_client:
        try:
            found = await resolve_osu_user(osu_api_client, query)
        except Exception as exc:
            logger.debug(f"find {query}: {exc}")
            found = None
        if found:
            osu_id, shown = found["id"], found["username"]

    async with get_db_session() as session:
        wanted = User.osu_user_id == osu_id if osu_id else func.lower(User.osu_username) == query.lower()
        linked = (await session.execute(select(User).where(
            User.chat_id == tenant_chat_id, User.osu_user_id.isnot(None), wanted))).scalars().all()

    people = []
    for user in linked:
        name = await _name_in_chat(message.bot, tenant_chat_id, user.telegram_id)
        if name:
            people.append(f'<a href="tg://user?id={user.telegram_id}">{escape_html(name)}</a>')
    if not people:
        await message.answer(format_error(t("find.nobody", lang, name=escape_html(shown)), lang), parse_mode="HTML")
        return
    await message.answer(t("find.found", lang, name=escape_html(shown), people=", ".join(people)),
                         parse_mode="HTML")


__all__ = ["router"]
