from aiogram.filters import BaseFilter
from aiogram import types

from config.settings import ADMIN_IDS


def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS


class AdminFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return is_admin(message.from_user.id)
