from typing import Callable, Dict, Any

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from db.database import AsyncSessionFactory
from db.models.user import User
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.timeutils import utcnow
from utils.titles import TITLE_REGISTRY
from utils.title_progress import detect_comeback, touch_activity_day, unlock_title

logger = get_logger("middleware.last_seen")

# Update at most once per 5 minutes per (user, group) to avoid DB spam.
_COOLDOWN_SECONDS = 300
_last_updated: Dict[tuple[int, int], float] = {}


def _event_chat(event) -> object | None:
    if isinstance(event, Message):
        return event.chat
    if isinstance(event, CallbackQuery):
        return event.message.chat if event.message else None
    return None


async def _announce_comeback(event, td) -> None:
    """Best-effort secret reveal for 'quit w' on the user's first message back."""
    try:
        target = event if isinstance(event, Message) else getattr(event, "message", None)
        if target is None or not event.from_user:
            return
        name = event.from_user.first_name or event.from_user.username or "Гражданин"
        await target.answer(
            f"🏅 <b>{escape_html(name)}</b> — новый титул: "
            f"{escape_html(td.name)} ({td.rarity_label})!",
            parse_mode="HTML",
        )
    except Exception:
        pass


class LastSeenMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: object, data: Dict[str, Any]) -> Any:
        import time

        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        chat = _event_chat(event)
        # last_seen is per-tenant: only group activity counts (it feeds duel
        # "online" detection). DMs have no group row, so they're skipped.
        chat_id = chat.id if chat and chat.type in ("group", "supergroup") else None

        if user_id and chat_id is not None:
            now_mono = time.monotonic()
            key = (user_id, chat_id)
            if now_mono - _last_updated.get(key, 0) > _COOLDOWN_SECONDS:
                _last_updated[key] = now_mono
                comeback_td = None
                try:
                    async with AsyncSessionFactory() as session:
                        user = (await session.execute(
                            select(User).where(
                                User.telegram_id == user_id, User.chat_id == chat_id)
                        )).scalar_one_or_none()
                        if user is not None:
                            # Comeback is read BEFORE last_seen_at is bumped.
                            came_back = detect_comeback(user)   # "quit w" (secret)
                            touch_activity_day(user)            # "Sleepless Watch" streak
                            user.last_seen_at = utcnow()
                            if came_back and await unlock_title(user, "comeback_180d", session):
                                comeback_td = TITLE_REGISTRY["comeback_180d"]
                            await session.commit()
                except Exception as e:
                    logger.debug(f"last_seen update failed for {user_id}@{chat_id}: {e}")
                if comeback_td is not None:
                    await _announce_comeback(event, comeback_td)

        return await handler(event, data)
