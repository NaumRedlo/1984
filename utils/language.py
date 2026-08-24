from sqlalchemy import select

from db.database import get_db_session
from db.models.user_language import UserLanguage

DEFAULT_LANGUAGE = "EN"


async def get_language(telegram_id: int) -> str:
    async with get_db_session() as session:
        row = (await session.execute(
            select(UserLanguage).where(UserLanguage.telegram_id == telegram_id)
        )).scalar_one_or_none()
        return row.language if row else DEFAULT_LANGUAGE


async def has_language(telegram_id: int) -> bool:
    async with get_db_session() as session:
        row = (await session.execute(
            select(UserLanguage).where(UserLanguage.telegram_id == telegram_id)
        )).scalar_one_or_none()
        return row is not None


async def set_language(telegram_id: int, language: str) -> None:
    lang = language.upper()
    async with get_db_session() as session:
        row = (await session.execute(
            select(UserLanguage).where(UserLanguage.telegram_id == telegram_id)
        )).scalar_one_or_none()
        if row:
            row.language = lang
        else:
            session.add(UserLanguage(telegram_id=telegram_id, language=lang))
        await session.commit()
