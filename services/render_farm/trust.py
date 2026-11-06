from config.settings import ADMIN_IDS
from utils.logger import get_logger

logger = get_logger(__name__)

KEY = "render_trusted_owners"

_owners: set[int] = set()


def trusted(telegram_id: int) -> bool:
    return bool(telegram_id) and (telegram_id in ADMIN_IDS or telegram_id in _owners)


def listed() -> list[int]:
    return sorted(_owners)


def _parse(value: str | None) -> set[int]:
    return {int(part) for part in (value or "").split(",") if part.strip().isdigit() and int(part) > 0}


async def load() -> int:
    try:
        from db.database import AsyncSessionFactory
        from db.models.bot_settings import BotSettings

        async with AsyncSessionFactory() as session:
            row = await session.get(BotSettings, KEY)
            _owners.clear()
            _owners.update(_parse(row.value if row else None))
    except Exception as exc:
        logger.warning("could not read the trusted render workers: %s", exc)
        return 0
    logger.info("render farm: %d trusted owner(s)", len(_owners))
    return len(_owners)


async def _save(owners: set[int]) -> None:
    from db.database import AsyncSessionFactory
    from db.models.bot_settings import BotSettings

    async with AsyncSessionFactory() as session:
        row = await session.get(BotSettings, KEY)
        value = ",".join(str(owner) for owner in sorted(owners))
        if row is None:
            session.add(BotSettings(key=KEY, value=value))
        else:
            row.value = value
        await session.commit()
    _owners.clear()
    _owners.update(owners)


async def grant(telegram_id: int) -> bool:
    if telegram_id <= 0 or telegram_id in _owners:
        return False
    await _save(_owners | {telegram_id})
    logger.info("render farm: owner %s is trusted", telegram_id)
    return True


async def revoke(telegram_id: int) -> bool:
    if telegram_id not in _owners:
        return False
    await _save(_owners - {telegram_id})
    logger.info("render farm: owner %s is no longer trusted", telegram_id)
    return True
