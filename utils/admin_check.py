from typing import Union

from aiogram.filters import BaseFilter
from aiogram import types

from config.settings import ADMIN_IDS


def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS


class AdminFilter(BaseFilter):
    async def __call__(self, event: Union[types.Message, types.CallbackQuery]) -> bool:
        user = event.from_user
        return bool(user and is_admin(user.id))
