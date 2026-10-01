import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import Date, DateTime, LargeBinary, delete, select

from db.models.dm_active_tenant import DmActiveTenant
from db.models.leaderboard_snapshot import LeaderboardSnapshot
from db.models.left_member import LeftMember
from db.models.player import SHARED, Player
from db.models.user import User
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("membership")

PAUSE = 0.05
WAIT_MOST = 30.0
KEPT_FOR = timedelta(days=365)
HERE = ("creator", "administrator", "member")
GONE = ("left", "kicked")
NOT_KEPT = ("id", "chat_id", "telegram_id", "player_id", "osu_user_id", "created_at", "updated_at", "oauth_access_token", "oauth_refresh_token", "oauth_token_expiry")

NO_BOT = "the bot is not in the chat"
EVERYONE = "everyone seems to be gone"

@dataclass
class Told:
    chat_id: int
    held: int = 0
    present: int = 0
    unknown: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: str = ""

def _own():
    return [column for column in User.__table__.columns if column.name not in NOT_KEPT and column.name not in SHARED and not isinstance(column.type, LargeBinary)]

def kept_of(user: User) -> str:
    said = {}
    for column in _own():
        value = getattr(user, column.name)
        if value is None:
            continue
        said[column.name] = value.isoformat() if isinstance(value, (datetime, date)) else value
    return json.dumps(said, ensure_ascii=False)

def give_back(user: User, kept: str) -> None:
    try:
        said = json.loads(kept or "{}")
    except ValueError:
        return
    if not isinstance(said, dict):
        return
    for column in _own():
        if column.name not in said:
            continue
        value = said[column.name]
        try:
            if isinstance(column.type, DateTime):
                value = datetime.fromisoformat(value)
            elif isinstance(column.type, Date):
                value = date.fromisoformat(value)
        except (TypeError, ValueError):
            continue
        setattr(user, column.name, value)

async def standing(bot, chat_id: int, telegram_id: int) -> Optional[bool]:
    if bot is None:
        return None
    try:
        member = await bot.get_chat_member(chat_id, telegram_id)
    except Exception as exc:
        wait = getattr(exc, "retry_after", None)
        if wait:
            await asyncio.sleep(min(float(wait), WAIT_MOST))
        return None
    status = getattr(member, "status", None)
    status = getattr(status, "value", status)
    if status in GONE:
        return False
    if status == "restricted":
        return bool(getattr(member, "is_member", True))
    if status in HERE:
        return True
    return None

async def leave(session, user: User, *, now: Optional[datetime] = None) -> None:
    now = now or utcnow()
    held = (await session.execute(
        select(LeftMember).where(LeftMember.chat_id == user.chat_id, LeftMember.telegram_id == user.telegram_id)
    )).scalar_one_or_none()
    if held is None:
        held = LeftMember(chat_id=user.chat_id, telegram_id=user.telegram_id)
        session.add(held)
    held.player_id, held.osu_user_id, held.osu_username = user.player_id, user.osu_user_id, user.osu_username
    held.kept, held.left_at = kept_of(user), now
    await session.execute(delete(LeaderboardSnapshot).where(LeaderboardSnapshot.user_id == user.id))
    await session.execute(delete(DmActiveTenant).where(DmActiveTenant.telegram_id == user.telegram_id, DmActiveTenant.chat_id == user.chat_id))
    player = await session.get(Player, user.player_id) if user.player_id is not None else None
    if player is not None and player.pinned_chat_id == user.chat_id:
        player.pinned_chat_id, player.pinned_at = None, None
    await session.delete(user)

async def join(session, player: Player, chat_id: int) -> Optional[User]:
    if player.telegram_id is None:
        return None
    taken = (await session.execute(
        select(User.id).where(User.chat_id == chat_id, (User.telegram_id == player.telegram_id) | (User.osu_user_id == player.osu_user_id))
    )).first()
    if taken is not None:
        return None
    shared = {name: getattr(player, name) for name in SHARED if hasattr(User, name) and getattr(player, name) is not None}
    user = User(chat_id=chat_id, telegram_id=player.telegram_id, osu_user_id=player.osu_user_id, **shared)
    held = (await session.execute(
        select(LeftMember).where(LeftMember.chat_id == chat_id, LeftMember.telegram_id == player.telegram_id)
    )).scalar_one_or_none()
    if held is not None:
        give_back(user, held.kept)
        await session.delete(held)
    session.add(user)
    return user

async def welcome_back(session, user: User) -> bool:
    held = (await session.execute(
        select(LeftMember).where(LeftMember.chat_id == user.chat_id, LeftMember.telegram_id == user.telegram_id)
    )).scalar_one_or_none()
    if held is None:
        return False
    give_back(user, held.kept)
    await session.delete(held)
    return True

async def gone(factory, chat_id: int, telegram_id: int) -> bool:
    async with factory() as session:
        user = (await session.execute(select(User).where(User.chat_id == chat_id, User.telegram_id == telegram_id))).scalar_one_or_none()
        if user is None:
            return False
        name = user.osu_username
        await leave(session, user)
        await session.commit()
    logger.info("%s left chat %s and is no longer counted there", name, chat_id)
    return True

async def came(factory, chat_id: int, telegram_id: int) -> bool:
    async with factory() as session:
        player = (await session.execute(select(Player).where(Player.telegram_id == telegram_id))).scalar_one_or_none()
        if player is None:
            return False
        user = await join(session, player, chat_id)
        if user is None:
            return False
        name = user.osu_username
        await session.commit()
    logger.info("%s is in chat %s and is counted there now", name, chat_id)
    return True

async def chats_known(session) -> list[int]:
    held = (await session.execute(select(User.chat_id).where(User.chat_id < 0).distinct())).scalars().all()
    left = (await session.execute(select(LeftMember.chat_id).distinct())).scalars().all()
    return sorted(set(held) | set(left))

async def sweep(bot, factory, *, dry: bool = False, pause: float = PAUSE, now: Optional[datetime] = None) -> list[Told]:
    now = now or utcnow()
    me = (await bot.get_me()).id
    async with factory() as session:
        chats = await chats_known(session)
        known = [(player.id, player.telegram_id, player.osu_user_id) for player in (await session.execute(select(Player).where(Player.telegram_id.isnot(None)))).scalars().all()]
    said: list[Told] = []
    for chat_id in chats:
        told = Told(chat_id)
        said.append(told)
        if await standing(bot, chat_id, me) is not True:
            told.skipped = NO_BOT
            continue
        async with factory() as session:
            rows = (await session.execute(select(User).where(User.chat_id == chat_id))).scalars().all()
            told.held = len(rows)
            absent = []
            for row in rows:
                answer = await standing(bot, chat_id, row.telegram_id)
                await asyncio.sleep(pause)
                if answer is True:
                    told.present += 1
                elif answer is False:
                    absent.append(row)
                else:
                    told.unknown += 1
            if len(rows) >= 2 and len(absent) == len(rows):
                told.skipped = EVERYONE
                absent = []
            here = {row.telegram_id for row in rows}
            played = {row.osu_user_id for row in rows if row.osu_user_id}
            newcomers = []
            for player_id, telegram_id, osu_user_id in known:
                if telegram_id in here or osu_user_id in played:
                    continue
                answer = await standing(bot, chat_id, telegram_id)
                await asyncio.sleep(pause)
                if answer is True:
                    newcomers.append(player_id)
            for row in absent:
                told.removed.append(row.osu_username)
                if not dry:
                    await leave(session, row, now=now)
            for player_id in newcomers:
                player = await session.get(Player, player_id)
                if player is None:
                    continue
                if dry:
                    told.added.append(player.osu_username)
                elif await join(session, player, chat_id) is not None:
                    told.added.append(player.osu_username)
            if not dry:
                await session.commit()
        if told.added or told.removed:
            logger.info("chat %s: %s came, %s left%s", chat_id, told.added, told.removed, " (not applied)" if dry else "")
    if not dry:
        async with factory() as session:
            await session.execute(delete(LeftMember).where(LeftMember.left_at < now - KEPT_FOR))
            await session.commit()
    return said
