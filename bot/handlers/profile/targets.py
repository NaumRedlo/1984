from dataclasses import dataclass
from typing import Optional

from aiogram import types

from bot.handlers.common.auth import require_registered_user
from db.database import get_db_session
from services.oauth.token_manager import get_valid_token
from utils.formatting.text import escape_html, format_error
from utils.i18n import t
from utils.logger import get_logger
from utils.osu.resolve_user import get_real_reply, get_registered_user, resolve_osu_user

logger = get_logger("handlers.targets")


@dataclass
class Target:
    osu_id: int
    name: str
    requester_tg_id: Optional[int] = None
    target_tg_id: Optional[int] = None


async def resolve_target(message: types.Message, query: str, tenant_chat_id, osu_api_client,
                         lang: str) -> Optional[Target]:
    """Whose plays a command is about, the way rs decides it: the player replied to, the one
    named, or the one asking. Answers the chat itself when nobody can be found."""
    tg_id = message.from_user.id
    query = (query or "").strip()

    real_reply = get_real_reply(message)
    if not query and real_reply and real_reply.from_user and real_reply.from_user.id != tg_id:
        async with get_db_session() as session:
            replied = await get_registered_user(session, real_reply.from_user.id, tenant_chat_id)
            requester = await get_registered_user(session, tg_id, tenant_chat_id)
        if replied and replied.osu_user_id:
            return Target(replied.osu_user_id, replied.osu_username,
                          requester.telegram_id if requester else None, replied.telegram_id)

    if not query:
        async with get_db_session() as session:
            user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
        if not user or not user.osu_user_id:
            return None
        return Target(user.osu_user_id, user.osu_username, user.telegram_id, user.telegram_id)

    async with get_db_session() as session:
        requester = await get_registered_user(session, tg_id, tenant_chat_id)
    try:
        found = await resolve_osu_user(osu_api_client, query)
    except Exception as exc:
        logger.error(f"Failed to find user {query}: {exc}")
        await message.answer(format_error(t("rs.search_error", lang, name=escape_html(query)), lang),
                             parse_mode="HTML")
        return None
    if not found:
        await message.answer(format_error(t("rs.player_not_found", lang, name=escape_html(query)), lang),
                             parse_mode="HTML")
        return None
    return Target(found["id"], found["username"], requester.telegram_id if requester else None)


async def token_for(target: Target) -> Optional[str]:
    token = await get_valid_token(target.requester_tg_id) if target.requester_tg_id else None
    if not token and target.target_tg_id and target.target_tg_id != target.requester_tg_id:
        token = await get_valid_token(target.target_tg_id)
    return token
