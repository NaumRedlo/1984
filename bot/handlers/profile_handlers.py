from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select
from db.models.user import User
from db.database import get_db_session

router = Router()

@router.message(Command("profile"))
async def show_profile(message: types.Message):
    """
    Показывает профиль пользователя: osu! ник, HPS, ранг и статистику.
    """
    tg_id = message.from_user.id

    async for session in get_db_session():
        try:
            # Ищем пользователя ПО telegram_id, а не по id
            stmt = select(User).where(User.telegram_id == tg_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                await message.answer(
                    "❌ Вы не зарегистрированы в системе.\n"
                    "Используйте `/register <osu_ник>` для начала.",
                    parse_mode="Markdown"
                )
                return

            # Формируем ответ
            profile_text = (
                f"👤 **Профиль:** `{message.from_user.full_name}`\n\n"
                f"🎮 **osu! ник:** `{user.osu_username}`\n"
                f"🆔 **osu! ID:** `{user.osu_user_id}`\n"
                f"📈 **Hunter Points:** {user.hps_points} HP\n"
                f"🏆 **Ранг:** {user.rank}\n"
                f"🎯 **Участий в баунти:** {user.bounties_participated}\n"
            )

            if user.last_active_bounty_id:
                profile_text += f"🏁 **Последний баунти:** `{user.last_active_bounty_id}`\n"

            profile_text += f"\n_Обновлено: {user.updated_at.strftime('%d.%m.%Y %H:%M')}_ "

            await message.answer(profile_text, parse_mode="Markdown")

        except Exception as e:
            print(f"Error in /profile for {tg_id}: {e}")
            await message.answer("❌ Произошла ошибка при загрузке профиля.")
            raise

__all__ = ["router"]
