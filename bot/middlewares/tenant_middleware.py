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

                data["tenant_chat_id"] = chat.id
            else:

                try:
                    async with AsyncSessionFactory() as session:
                        data["tenant_chat_id"] = await effective_tenant(event, session)
                except Exception as e:
                    logger.debug(f"tenant resolve failed: {e}")
                    data["tenant_chat_id"] = None
        return await handler(event, data)
