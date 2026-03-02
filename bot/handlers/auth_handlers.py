from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.models.user import User
from db.database import get_db_session
from contextlib import asynccontextmanager

# --- ВАЖНО: Не импортируем api_client напрямую из bot.main или utils ---
# Клиент будет передан через middleware в kwargs хендлера.
# from utils import osu_api_client  # <-- УДАЛЕНО
# from bot.main import osu_api_client_instance as api_client_obj # <-- УДАЛЕНО

router = Router()

@asynccontextmanager
async def get_session():
    """
    Контекстный менеджер для получения сессии базы данных.
    Обеспечивает commit при успехе и rollback при ошибке.
    """
    async for session in get_db_session():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise  # Переподнимаем исключение, чтобы ошибка не прошла незамеченной

@router.message(Command("register"))
async def register_user(message: types.Message, **kwargs):
    """
    Обработчик команды /register <osu_username>.
    - Проверяет, зарегистрирован ли уже пользователь по telegram_id.
    - Проверяет существование osu! пользователя через API.
    - Создаёт или обновляет запись в базе данных.
    - Отправляет пользователю подтверждающее сообщение.
    """
    # --- 1. Получаем api_client из kwargs, переданных middleware ---
    api_client = kwargs.get("osu_api_client")
    if not api_client:
        # Это критическая ошибка, если middleware не сработал.
        await message.answer("❌ Ошибка: API клиент не инициализирован. Попробуйте позже.")
        return

    # --- 2. Парсим аргумент команды ---
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат команды.\n"
            "Используйте: `/register <ваш_osu_ник>`",
            parse_mode="Markdown"
        )
        return

    osu_username_provided = args[1].strip()
    tg_id = message.from_user.id

    # --- 3. Проверяем пользователя через osu! API ---
    user_data = await api_client.get_user_by_name(osu_username_provided)
    if not user_data:
        await message.answer(
            f"❌ Пользователь `{osu_username_provided}` не найден в osu!.",
            parse_mode="Markdown"
        )
        return

    # --- 4. Работаем с базой данных ---
    async for session in get_db_session(): # <-- Используем сессию из db.database
        try:
            # --- 4.1. Проверяем, существует ли пользователь в БД по telegram_id ---
            stmt = select(User).where(User.telegram_id == tg_id)
            result = await session.execute(stmt)
            existing_user = result.scalar_one_or_none() # <-- Получаем одного или None

            if existing_user:
                # --- Пользователь уже зарегистрирован ---
                await message.answer(
                    f"✅ Вы уже зарегистрированы в системе.\n"
                    f"Telegram: `{message.from_user.full_name}`\n"
                    f"osu!: `{existing_user.osu_username}` (ID: {existing_user.osu_user_id})\n"
                    f"HPS: {existing_user.hps_points} HP\n"
                    f"Ранг: {existing_user.rank}",
                    parse_mode="Markdown"
                )
                return # <-- Возвращаемся, не создавая нового пользователя

            # --- 4.2. Создаём нового пользователя ---
            new_user = User(
                telegram_id=tg_id,
                osu_username=osu_username_provided,
                osu_user_id=user_data.get('id'),  # <-- Сохраняем osu! ID
                # hps_points по умолчанию 0, rank по умолчанию "Candidate"
            )
            session.add(new_user)
            await session.commit() # <-- Сохраняем изменения

            # --- 4.3. Отправляем подтверждение пользователю ---
            await message.answer(
                f"✅ Регистрация в системе прошла успешно!\n"
                f"Telegram: `{message.from_user.full_name}`\n"
                f"osu!: `{osu_username_provided}` (ID: {user_data.get('id')})\n"
                f"HPS: {new_user.hps_points} HP\n"
                f"Ранг: {new_user.rank}",
                parse_mode="Markdown"
            )
            return # <-- Успешное завершение

        except Exception as e:
            # --- 5. Обработка ошибок ---
            await session.rollback()
            # Логируем ошибку в консоль (если используется логгер, логируйте туда)
            print(f"Ошибка при обработке /register для {tg_id}: {e}")
            # Отправляем пользователю сообщение об ошибке
            await message.answer(
                "❌ Произошла ошибка при регистрации. Попробуйте позже."
            )
            # Переподнимаем исключение для логирования в aiogram
            raise

__all__ = ["router"]
