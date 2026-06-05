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

from db.models.user import User

_GROUP_TYPES = {"group", "supergroup"}


def _chat_of(event) -> Optional[object]:
    if isinstance(event, Message):
        return event.chat
    if isinstance(event, CallbackQuery):
        return event.message.chat if event.message else None
    return getattr(event, "chat", None)


def tenant_id(event) -> Optional[int]:
    """Tenant (group chat_id) for a Message/CallbackQuery, or None in a DM /
    channel. Use this for group-scoped commands."""
    chat = _chat_of(event)
    if chat is None:
        return None
    return chat.id if chat.type in _GROUP_TYPES else None


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


__all__ = ["tenant_id", "group_only_notice", "active_tenants"]
