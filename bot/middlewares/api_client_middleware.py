from aiogram import BaseMiddleware
from typing import Callable, Dict, Any

class ApiClientMiddleware(BaseMiddleware):
    """
    Middleware for passing the OsuApiClient instance to handlers.
    """
    def __init__(self, api_client):
        super().__init__()
        self.api_client = api_client

    async def __call__(
        self,
        handler: Callable,
        event: object, # This can be a Message or CallbackQuery.
        data: Dict[str, Any]
    ) -> Any:
        """
        Called before each handler.
        Adds api_client to the data dictionary.
        """
        # Add our client to data
        data["osu_api_client"] = self.api_client
        # Call the next handler (or other middleware)
        return await handler(event, data)

