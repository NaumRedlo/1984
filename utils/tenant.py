from __future__ import annotations

from typing import Optional

from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.dm_active_tenant import DmActiveTenant
from db.models.user import User
from utils.i18n import t
from utils.language import get_language

_GROUP_TYPES = {"group", "supergroup"}


def _chat_of(event) -> Optional[object]:
    if isinstance(event, Message):
        return event.chat
    if isinstance(event, CallbackQuery):
        return event.message.chat if event.message else None
    return getattr(event, "chat", None)


def _telegram_id_of(event) -> Optional[int]:
    user = getattr(event, "from_user", None)
    return int(user.id) if user is not None else None


def tenant_id(event) -> Optional[int]:
    chat = _chat_of(event)
    if chat is None:
        return None
    return chat.id if chat.type in _GROUP_TYPES else None



async def user_tenants(session: AsyncSession, telegram_id: int) -> list[int]:
    rows = (await session.execute(
        select(User.chat_id)
        .where(User.telegram_id == telegram_id)
        .order_by(User.id.desc())
    )).scalars().all()
    seen: set[int] = set()
    ordered: list[int] = []
    for c in rows:
        if c is None or c in seen:
            continue
        seen.add(c)
        ordered.append(c)
    return ordered


async def get_dm_tenant(session: AsyncSession, telegram_id: int) -> Optional[int]:
    chat_id = (await session.execute(
        select(DmActiveTenant.chat_id).where(DmActiveTenant.telegram_id == telegram_id)
    )).scalar_one_or_none()
    if chat_id is None:
        return None

    still_member = (await session.execute(
        select(User.id).where(
            User.chat_id == chat_id, User.telegram_id == telegram_id,
        ).limit(1)
    )).scalar_one_or_none()
    if still_member is None:
        await clear_dm_tenant(session, telegram_id)
        return None
    return chat_id


async def set_dm_tenant(session: AsyncSession, telegram_id: int, chat_id: int) -> None:
    row = (await session.execute(
        select(DmActiveTenant).where(DmActiveTenant.telegram_id == telegram_id)
    )).scalar_one_or_none()
    if row is None:
        session.add(DmActiveTenant(telegram_id=telegram_id, chat_id=chat_id))
    else:
        row.chat_id = chat_id
    await session.commit()


async def clear_dm_tenant(session: AsyncSession, telegram_id: int) -> None:
    row = (await session.execute(
        select(DmActiveTenant).where(DmActiveTenant.telegram_id == telegram_id)
    )).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()


async def effective_tenant(event, session: AsyncSession) -> Optional[int]:
    chat = _chat_of(event)
    if chat is None:
        return None
    if chat.type in _GROUP_TYPES:
        return chat.id
    tg_id = _telegram_id_of(event)
    if tg_id is None:
        return None
    return await get_dm_tenant(session, tg_id)


async def group_only_notice(event) -> None:
    tg_id = _telegram_id_of(event)
    lang = (await get_language(tg_id)).lower() if tg_id is not None else "en"
    text = t("common.group_only", lang)
    if isinstance(event, Message):
        await event.answer(text)
    elif isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)


async def active_tenants(session: AsyncSession) -> list[int]:
    rows = (await session.execute(select(User.chat_id).distinct())).scalars().all()
    return [c for c in rows if c is not None]


__all__ = [
    "tenant_id",
    "group_only_notice",
    "active_tenants",
    "effective_tenant",
    "user_tenants",
    "get_dm_tenant",
    "set_dm_tenant",
    "clear_dm_tenant",
]
