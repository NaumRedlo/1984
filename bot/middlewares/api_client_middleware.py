from aiogram import BaseMiddleware
from typing import Callable, Dict, Any

class ApiClientMiddleware(BaseMiddleware):
    def __init__(self, api_client):
        super().__init__()
        self.api_client = api_client

    async def __call__(
        self,
        handler: Callable,
        event: object,
        data: Dict[str, Any]
    ) -> Any:
        data["osu_api_client"] = self.api_client
        return await handler(event, data)

