from typing import Union

from aiogram import types
from aiogram.filters import BaseFilter

from config.settings import RENDER_TESTER_IDS


def can_use_render(telegram_id: int) -> bool:
    return telegram_id in RENDER_TESTER_IDS


class RenderTesterFilter(BaseFilter):
    async def __call__(self, event: Union[types.Message, types.CallbackQuery]) -> bool:
        user = event.from_user
        return bool(user and can_use_render(user.id))
