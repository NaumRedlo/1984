from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.user import User
from services.oauth.token_manager import has_oauth
from utils.osu.resolve_user import get_registered_user


@dataclass(slots=True)
class EffectiveAuthState:
    user: Optional[User]
    is_registered: bool
    has_linked_oauth: bool


async def get_effective_auth_state(session: AsyncSession, telegram_id: int) -> EffectiveAuthState:
    user = await get_registered_user(session, telegram_id)
    if not user:
        return EffectiveAuthState(user=None, is_registered=False, has_linked_oauth=False)

    linked_oauth = await has_oauth(user.id)
    return EffectiveAuthState(user=user, is_registered=True, has_linked_oauth=linked_oauth)


async def require_registered_user(
    session: AsyncSession,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> Optional[User]:
    actor = message.from_user if message else callback.from_user if callback else None
    if not actor:
        return None

    user = await get_registered_user(session, actor.id)
    if user:
        return user

    text = (
        "Вы не зарегистрированы.\n"
        "Используйте <code>register &lt;osu_nickname&gt;</code>"
    )
    if message:
        await message.answer(text, parse_mode="HTML")
    elif callback:
        await callback.answer("Сначала зарегистрируйтесь.", show_alert=True)
    return None


async def require_linked_oauth(
    session: AsyncSession,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> Optional[User]:
    user = await require_registered_user(session, message=message, callback=callback)
    if not user:
        return None

    if await has_oauth(user.id):
        return user

    text = "Сначала привяжите osu! OAuth: <code>link</code>"
    if message:
        await message.answer(text, parse_mode="HTML")
    elif callback:
        await callback.answer("Сначала привяжите osu! OAuth через link.", show_alert=True)
    return None


async def validate_callback_owner(callback: CallbackQuery, owner_tg_id: int, text: str = "Это не ваша карточка.") -> bool:
    if callback.from_user.id == owner_tg_id:
        return True

    await callback.answer(text, show_alert=True)
    return False
