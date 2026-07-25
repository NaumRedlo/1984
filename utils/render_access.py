"""Who is allowed near the render engine.

Separate from `utils.admin_check` on purpose. An admin runs the bot; a render
tester runs an unfinished simulator that shells out to a native binary and
downloads beatmaps on demand. Those are different kinds of trust, and folding
them together would silently hand the second to everyone who has the first.

Empty list = nobody, including admins. That's the intended default: while the
engine is under test it should ignore the world rather than answer it.
"""

from typing import Union

from aiogram import types
from aiogram.filters import BaseFilter

from config.settings import RENDER_TESTER_IDS


def can_use_render(telegram_id: int) -> bool:
    return telegram_id in RENDER_TESTER_IDS


class RenderTesterFilter(BaseFilter):
    """Router-level gate. Non-testers fall through as if the handler didn't
    exist — no refusal message, because a half-built feature shouldn't announce
    itself to people who can't use it."""

    async def __call__(self, event: Union[types.Message, types.CallbackQuery]) -> bool:
        user = event.from_user
        return bool(user and can_use_render(user.id))
