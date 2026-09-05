from typing import Any, Callable, Dict

from aiogram import BaseMiddleware

class GroupRestrictionMiddleware(BaseMiddleware):

    async def __call__(
        self,
        handler: Callable,
        event: object,
        data: Dict[str, Any],
    ) -> Any:
        return await handler(event, data)
