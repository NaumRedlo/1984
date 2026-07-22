"""Map-request handlers: the `req` wizard and the `reqs` hub (inbox / tasks /
sent) plus accept/decline/cancel. Assembled under one router for main.py."""

from aiogram import Router

from bot.handlers.requests.wizard import router as wizard_router
from bot.handlers.requests.hub import router as hub_router

router = Router(name="requests_combined")
router.include_router(wizard_router)
router.include_router(hub_router)

__all__ = ["router"]
