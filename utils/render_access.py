from typing import Union

from aiogram import types
from aiogram.filters import BaseFilter

from config import settings


def can_use_render(telegram_id: int) -> bool:
    """Whether this person may reach anything render-related.

    Read off the settings module rather than imported from it, so that a test —
    and a future admin command — can move the gate without reloading every
    module that ever asked. The values themselves still come from the
    environment at startup; this only decides where the question is answered.
    """
    if settings.RENDER_OPEN_TO_ALL:
        return True
    return telegram_id in settings.RENDER_TESTER_IDS


class RenderTesterFilter(BaseFilter):
    async def __call__(self, event: Union[types.Message, types.CallbackQuery]) -> bool:
        user = event.from_user
        return bool(user and can_use_render(user.id))
