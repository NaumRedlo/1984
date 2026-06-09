"""Inject the *effective tenant* (group chat_id to scope data by) into handlers.

In a group the tenant is the chat itself; in a private chat it's whichever group
the user picked for DM (see ``utils.tenant.effective_tenant``). Handlers read it
as the ``tenant_chat_id`` kwarg — the same injection pattern as ``osu_api_client``
— and scope their data by it instead of ``message.chat.id``. This is *injection
only*: the gate (prompting a DM user to choose a group) lives in the data
handlers via ``ensure_dm_tenant``, so service/admin/auth commands stay untouched.
"""

from typing import Any, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from db.database import AsyncSessionFactory
from utils.logger import get_logger
from utils.tenant import _chat_of, effective_tenant

logger = get_logger("middleware.tenant")

_GROUP_TYPES = {"group", "supergroup"}


class TenantMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: object, data: Dict[str, Any]) -> Any:
        if isinstance(event, (Message, CallbackQuery)):
            chat = _chat_of(event)
            if chat is not None and chat.type in _GROUP_TYPES:
                # Group: tenant is the chat itself — no DB lookup needed (the
                # hot path, every group message/callback).
                data["tenant_chat_id"] = chat.id
            else:
                # DM/channel: resolve the user's stored group choice.
                try:
                    async with AsyncSessionFactory() as session:
                        data["tenant_chat_id"] = await effective_tenant(event, session)
                except Exception as e:
                    logger.debug(f"tenant resolve failed: {e}")
                    data["tenant_chat_id"] = None
        return await handler(event, data)
