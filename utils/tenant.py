"""Multi-tenant helpers.

Every player record (and everything hanging off it) is scoped to the Telegram
group it was registered in — the group's ``chat.id`` is the tenant key, stored
on ``users.chat_id``. These helpers resolve "which tenant is this event acting
on" and enumerate the active tenants for per-group background work.

Rule of thumb: the tenant is simply the chat the message/callback lives in. In
a private chat there is no group, so group-scoped ("rank") commands have no
tenant — :func:`tenant_id` returns ``None`` and the handler should refuse with
:func:`group_only_notice`.
"""

from __future__ import annotations

from typing import Optional

from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.dm_active_tenant import DmActiveTenant
from db.models.user import User

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
    """Tenant (group chat_id) for a Message/CallbackQuery, or None in a DM /
    channel. Use this for group-scoped commands."""
    chat = _chat_of(event)
    if chat is None:
        return None
    return chat.id if chat.type in _GROUP_TYPES else None


# ── DM tenant selection ──────────────────────────────────────────────────────
#
# In a private chat there is no group, so the user picks which group's data the
# bot should act on. The choice is stored per Telegram identity (global, like
# OAuth) in ``dm_active_tenant`` and resolved into an "effective tenant" that the
# rest of the handlers scope their data by — exactly the role ``chat.id`` plays
# in a group.

async def user_tenants(session: AsyncSession, telegram_id: int) -> list[int]:
    """Distinct group chat_ids this Telegram identity is registered in.

    Ordered by most-recent registration first (highest ``users.id``), so the
    picker lists the freshest group at the top and a single-group user gets that
    group auto-selected.
    """
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
    """The group chat_id this identity chose for DM, or None if unset/stale.

    Self-heals: if the stored group is no longer one the user is registered in
    (e.g. they left the group / unlinked there), the row is dropped and None is
    returned so the user is re-prompted to choose.
    """
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
    """Persist (upsert) the user's chosen DM tenant group."""
    row = (await session.execute(
        select(DmActiveTenant).where(DmActiveTenant.telegram_id == telegram_id)
    )).scalar_one_or_none()
    if row is None:
        session.add(DmActiveTenant(telegram_id=telegram_id, chat_id=chat_id))
    else:
        row.chat_id = chat_id
    await session.commit()


async def clear_dm_tenant(session: AsyncSession, telegram_id: int) -> None:
    """Forget the user's DM tenant choice (next DM command re-prompts)."""
    row = (await session.execute(
        select(DmActiveTenant).where(DmActiveTenant.telegram_id == telegram_id)
    )).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()


async def effective_tenant(event, session: AsyncSession) -> Optional[int]:
    """Tenant to scope this event's data by.

    Group chat → the group ``chat.id`` (unchanged behaviour). Private chat → the
    user's stored DM tenant (or None if they haven't chosen one yet / it went
    stale). None means "no data scope" — the caller should prompt a group pick.
    """
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
    """Tell the user a command only works inside a group chat."""
    text = "Эта команда работает только в беседе."
    if isinstance(event, Message):
        await event.answer(text)
    elif isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)


async def active_tenants(session: AsyncSession) -> list[int]:
    """Distinct group chat_ids that have at least one registered user — the set
    of tenants per-group background tasks must fan out over."""
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
