from typing import Any, Callable, Dict

from aiogram import BaseMiddleware


class GroupRestrictionMiddleware(BaseMiddleware):
    """No-op pass-through (kept for wiring/revert compatibility).

    The bot is now **multi-tenant**: it is usable in any group, and data is
    isolated per group via ``users.chat_id`` (see ``utils/tenant.py``). There is
    no single allowed group to gate access on anymore — participation in a chat
    is gated by per-group registration at the handler level, not here. The old
    ``GROUP_CHAT_ID`` membership lock has been removed.
    """

    async def __call__(
        self,
        handler: Callable,
        event: object,
        data: Dict[str, Any],
    ) -> Any:
        return await handler(event, data)
