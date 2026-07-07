from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.user import User
from services.oauth.token_manager import has_oauth
from utils.i18n import t
from utils.language import get_language
from utils.osu.resolve_user import get_registered_user
from bot.handlers.dm_tenant import ensure_dm_tenant


@dataclass(slots=True)
class EffectiveAuthState:
    user: Optional[User]
    is_registered: bool
    has_linked_oauth: bool


async def get_effective_auth_state(
    session: AsyncSession, telegram_id: int, chat_id: int,
) -> EffectiveAuthState:
    user = await get_registered_user(session, telegram_id, chat_id)
    if not user:
        return EffectiveAuthState(user=None, is_registered=False, has_linked_oauth=False)

    linked_oauth = await has_oauth(user.telegram_id)
    return EffectiveAuthState(user=user, is_registered=True, has_linked_oauth=linked_oauth)


async def require_registered_user(
    session: AsyncSession,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    tenant_chat_id: Optional[int] = None,
) -> Optional[User]:
    actor = message.from_user if message else callback.from_user if callback else None
    event = message or callback
    if not actor or event is None:
        return None

    # Player data is per-group (users.chat_id). ``tenant_chat_id`` is the
    # effective tenant injected by TenantMiddleware: the group itself in a group
    # chat, or the user's chosen group in a DM. If it's unset (DM, no group
    # picked yet) ``ensure_dm_tenant`` shows the group picker and we stop.
    if not await ensure_dm_tenant(event, tenant_chat_id):
        return None
    chat_id = tenant_chat_id

    user = await get_registered_user(session, actor.id, chat_id)
    if user:
        return user

    lang = (await get_language(actor.id)).lower()
    if message:
        await message.answer(t("auth.not_registered", lang), parse_mode="HTML")
    elif callback:
        await callback.answer(t("auth.not_registered_alert", lang), show_alert=True)
    return None


async def require_linked_oauth(
    session: AsyncSession,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    tenant_chat_id: Optional[int] = None,
) -> Optional[User]:
    user = await require_registered_user(
        session, message=message, callback=callback, tenant_chat_id=tenant_chat_id)
    if not user:
        return None

    if await has_oauth(user.telegram_id):
        return user

    lang = (await get_language(user.telegram_id)).lower()
    if message:
        await message.answer(t("auth.link_first", lang), parse_mode="HTML")
    elif callback:
        await callback.answer(t("auth.link_first_alert", lang), show_alert=True)
    return None


async def validate_callback_owner(callback: CallbackQuery, owner_tg_id: int, text: str | None = None) -> bool:
    if callback.from_user.id == owner_tg_id:
        return True

    if text is None:
        lang = (await get_language(callback.from_user.id)).lower()
        text = t("auth.not_your_card", lang)
    await callback.answer(text, show_alert=True)
    return False
