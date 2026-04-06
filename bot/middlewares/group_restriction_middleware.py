from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any

from config.settings import GROUP_CHAT_ID, ADMIN_IDS
from utils.logger import get_logger

logger = get_logger("middleware.group_restriction")


class GroupRestrictionMiddleware(BaseMiddleware):
    """
    Restricts bot usage to members of a specific group.
    If GROUP_CHAT_ID is not set, the middleware is a no-op.
    Fail-closed: if membership check fails, access is denied (except for admins).
    """

    async def __call__(
        self,
        handler: Callable,
        event: object,
        data: Dict[str, Any],
    ) -> Any:
        if not GROUP_CHAT_ID:
            return await handler(event, data)

        # Extract user_id and bot from event
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            chat_id = event.chat.id
            bot: Bot = event.bot
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            chat_id = event.message.chat.id if event.message else None
            bot: Bot = event.bot
        else:
            return await handler(event, data)

        if not user_id:
            return

        # Allow messages sent directly in the group
        if chat_id == GROUP_CHAT_ID:
            return await handler(event, data)

        # Admins always pass
        if user_id in ADMIN_IDS:
            return await handler(event, data)

        # For private chats, verify user is a member of the group
        try:
            member = await bot.get_chat_member(GROUP_CHAT_ID, user_id)
            if member.status in ("left", "kicked"):
                logger.debug(f"Blocked non-member {user_id} from using bot")
                if isinstance(event, Message):
                    await event.answer(
                        "You must be a member of the group to use this bot.",
                    )
                return
        except Exception as e:
            # Fail-closed: deny access if we can't verify membership
            logger.warning(f"Failed to check membership for {user_id}, denying access: {e}")
            if isinstance(event, Message):
                await event.answer(
                    "Cannot verify group membership. Please try again later.",
                )
            return

        return await handler(event, data)
