from typing import Union

from aiogram import types
from aiogram.filters import BaseFilter

from config import settings

def can_use_render(telegram_id: int) -> bool:
    if settings.RENDER_OPEN_TO_ALL:
        return True
    return telegram_id in settings.RENDER_TESTER_IDS

class RenderTesterFilter(BaseFilter):
    async def __call__(self, event: Union[types.Message, types.CallbackQuery]) -> bool:
        user = event.from_user
        return bool(user and can_use_render(user.id))
