from datetime import datetime, timedelta, timezone
import asyncio

from aiogram import Router, types
from sqlalchemy import delete, select

from bot.filters import TextTriggerFilter, TriggerArgs
from config.settings import ADMIN_IDS, GROUP_CHAT_ID
from db.database import get_db_session
from db.models.best_score import UserBestScore
from db.models.duel import Duel
from db.models.duel_round import DuelRound
from db.models.map_attempt import UserMapAttempt
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from db.models.oauth_token import OAuthToken
from utils.logger import get_logger
from utils.osu.resolve_user import (
    get_any_user_by_telegram_id,
    get_registered_user,
    resolve_osu_user,
)
from utils.formatting.text import escape_html, format_error, format_success
from services.oauth.server import generate_oauth_url, track_link_message
from services.oauth.token_manager import has_oauth
from bot.handlers.common.auth import get_effective_auth_state
from services.refresh import refresh_user

logger = get_logger("handlers.auth")
router = Router(name="auth")
UNLINK_COOLDOWN_DAYS = 30


async def _is_group_member(bot, user_id: int) -> bool:
    if not GROUP_CHAT_ID:
        return True
    try:
        member = await bot.get_chat_member(GROUP_CHAT_ID, user_id)
        return member.status not in ("left", "kicked")
    except Exception as e:
        logger.warning(f"Failed to check group membership for {user_id}: {e}")
        return False


async def _can_unlink(user: User) -> tuple[bool, str | None]:
    if not user.last_unlink_at:
        return True, None

    now_utc = datetime.now(timezone.utc)
    last_unlink = user.last_unlink_at
    if last_unlink.tzinfo is None:
        last_unlink = last_unlink.replace(tzinfo=timezone.utc)

    elapsed = now_utc - last_unlink
    if elapsed >= timedelta(days=UNLINK_COOLDOWN_DAYS):
        return True, None

    remaining = timedelta(days=UNLINK_COOLDOWN_DAYS) - elapsed
    days = remaining.days
    hours = remaining.seconds // 3600
    return False, f"{days}д {hours}ч"


async def _clear_user_cache(session, user: User) -> None:
    await session.execute(delete(UserBestScore).where(UserBestScore.user_id == user.id))
    await session.execute(delete(UserMapAttempt).where(UserMapAttempt.user_id == user.id))
    await session.execute(delete(UserTitleProgress).where(UserTitleProgress.user_id == user.id))

    duel_stmt = select(Duel).where(
        (Duel.player1_user_id == user.id)
        | (Duel.player2_user_id == user.id)
        | (Duel.winner_user_id == user.id)
    )
    duels = (await session.execute(duel_stmt)).scalars().all()
    duel_ids = [d.id for d in duels]
    if duel_ids:
        await session.execute(delete(DuelRound).where(DuelRound.duel_id.in_(duel_ids)))
        for duel in duels:
            await session.delete(duel)


@router.message(TextTriggerFilter("register", "reg"))
async def register_user(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    tg_id = message.from_user.id
    raw_username = trigger_args.args

    if not raw_username:
        await message.answer(
            "<b>Укажите ваш osu! никнейм или ID:</b>\n"
            "<code>register Nickname</code> или <code>register id:12345</code>",
            parse_mode="HTML",
        )
        return

    if not await _is_group_member(message.bot, tg_id):
        await message.answer(
            format_error("Регистрация доступна только для участников группы."),
            parse_mode="HTML",
        )
        return

    wait_msg = await message.answer(f"Поиск в базе osu!: <b>{escape_html(raw_username)}</b>...", parse_mode="HTML")

    try:
        user_data = await resolve_osu_user(osu_api_client, raw_username)
        if not user_data:
            await wait_msg.edit_text(
                format_error(f"Пользователь <b>{escape_html(raw_username)}</b> не найден в базе osu!."),
                parse_mode="HTML",
            )
            return

        osu_id = user_data["id"]
        osu_name = user_data["username"]

        async with get_db_session() as session:
            existing_osu = (
                await session.execute(
                    select(User).where(User.osu_user_id == osu_id, User.telegram_id != tg_id)
                )
            ).scalar_one_or_none()
            if existing_osu:
                await wait_msg.edit_text(
                    format_error(f"Аккаунт osu! <b>{escape_html(osu_name)}</b> уже привязан к другому пользователю."),
                    parse_mode="HTML",
                )
                return

            user = await get_any_user_by_telegram_id(session, tg_id)
            if user and user.osu_user_id and user.osu_user_id != osu_id and tg_id not in ADMIN_IDS:
                await wait_msg.edit_text(
                    format_error(
                        f"Ваш профиль уже привязан к <b>{escape_html(user.osu_username)}</b>.\n"
                        "Перепривязка доступна только администраторам."
                    ),
                    parse_mode="HTML",
                )
                return

            if not user:
                user = User(
                    telegram_id=tg_id,
                    osu_user_id=osu_id,
                    osu_username=osu_name,
                    player_pp=int(user_data["pp"]),
                    global_rank=user_data["global_rank"] or 0,
                    country=user_data["country_code"],
                    accuracy=round(user_data["accuracy"], 2),
                    play_count=user_data["play_count"],
                    play_time=int(user_data.get("play_time", 0)),
                    ranked_score=int(user_data.get("ranked_score", 0)),
                    total_hits=int(user_data.get("total_hits", 0)),
                    last_api_update=datetime.now(timezone.utc),
                )
                session.add(user)
                action_text = "зарегистрирован"
            else:
                user.osu_user_id = osu_id
                user.osu_username = osu_name
                user.player_pp = int(user_data["pp"])
                user.global_rank = user_data["global_rank"] or 0
                user.country = user_data["country_code"]
                user.accuracy = round(user_data["accuracy"], 2)
                user.play_count = user_data["play_count"]
                user.play_time = int(user_data.get("play_time", 0))
                user.ranked_score = int(user_data.get("ranked_score", 0))
                user.total_hits = int(user_data.get("total_hits", 0))
                user.last_api_update = datetime.now(timezone.utc)
                action_text = "перепривязан"

            await session.commit()

            await refresh_user(user, session, osu_api_client, mode="full")
            await session.commit()
            await session.refresh(user)

        await wait_msg.edit_text(
            f"<b>Личность подтверждена!</b>\n\n"
            f"Пользователь <code>{osu_name}</code> {action_text} в системе Project 1984.\n"
            f"Ранг: <code>#{user_data['global_rank']:,}</code>\n"
            f"PP: <code>{user_data['pp']:,}</code>",
            parse_mode="HTML",
        )
        logger.info(f"User {tg_id} successfully {action_text} as {osu_name} (ID: {osu_id})")

    except Exception as e:
        logger.error(f"Failed to register user {tg_id}: {e}", exc_info=True)
        await wait_msg.edit_text(format_error("Системная ошибка при верификации."))


@router.message(TextTriggerFilter("link"))
async def link_oauth(message: types.Message):
    tg_id = message.from_user.id

    async with get_db_session() as session:
        auth_state = await get_effective_auth_state(session, tg_id)
        user = auth_state.user

    if not auth_state.is_registered or not user:
        await message.answer(
            format_error("Сначала зарегистрируйтесь: <code>register &lt;nickname&gt;</code>"),
            parse_mode="HTML",
        )
        return

    if auth_state.has_linked_oauth:
        msg = await message.answer(
            f"Аккаунт <b>{escape_html(user.osu_username)}</b> уже привязан к системе.",
            parse_mode="HTML",
        )
        await asyncio.sleep(5)
        await msg.delete()
        return

    url = generate_oauth_url(tg_id)

    sent = await message.answer(
        f"🔗 <b>Привязка osu! OAuth</b>\n\n"
        f"Перейдите по ссылке и авторизуйтесь:\n"
        f"<a href=\"{url}\">Авторизоваться в osu!</a>\n\n"
        f"После авторизации вернитесь в Telegram.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    track_link_message(tg_id, sent.chat.id, sent.message_id)


@router.message(TextTriggerFilter("unlink", "unregister", "unreg"))
async def unlink_user(message: types.Message):
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user or not user.osu_user_id:
            await message.answer("Ваш профиль не привязан к osu! аккаунту.")
            return

        can_unlink, remaining = await _can_unlink(user)
        if not can_unlink:
            await message.answer(
                format_error(f"Отвязка доступна раз в месяц. Повторите через {remaining}."),
                parse_mode="HTML",
            )
            return

        await _clear_user_cache(session, user)
        await session.execute(
            delete(OAuthToken).where(OAuthToken.user_id == user.id)
        )

        user.osu_user_id = None
        user.player_pp = 0
        user.global_rank = 0
        user.country = "XX"
        user.accuracy = 0.0
        user.play_count = 0
        user.play_time = 0
        user.ranked_score = 0
        user.total_hits = 0
        user.total_score = 0
        user.avatar_url = None
        user.cover_url = None
        user.avatar_data = None
        user.cover_data = None
        user.hps_points = 0
        user.rank = "Candidate"
        user.bounties_participated = 0
        user.last_active_bounty_id = None
        user.active_title_code = None
        user.duel_wins = 0
        user.duel_losses = 0
        user.last_api_update = None
        user.oauth_access_token = None
        user.oauth_refresh_token = None
        user.oauth_token_expiry = None
        user.last_unlink_at = datetime.now(timezone.utc)

        await session.commit()

    await message.answer(
        format_success("Привязка osu! аккаунта удалена. Повторная отвязка доступна через месяц."),
        parse_mode="HTML",
    )


__all__ = ["router"]
