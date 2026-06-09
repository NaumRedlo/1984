from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.duel.common import (
    LOOKING_TIMEOUT,
    ONLINE_THRESHOLD,
    _looking_for_duel,
    build_duel_keyboard,
    build_duel_panel_keyboard,
    dm,
    get_duel_data,
    resolve_duel_thread,
)
from bot.handlers.duel.duel import handle_challenge
from bot.handlers.dm_tenant import ensure_dm_tenant
from db.database import get_db_session
from db.models.duel import Duel
from db.models.duel_rating import DuelRating
from db.models.user import User
from bot.utils.safe_edit import safe_edit_media
from services.image import card_renderer
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.osu.resolve_user import get_any_user_by_telegram_id

router = Router(name="duel.profile_panel")
logger = get_logger("handlers.duel.profile_panel")


@router.message(TextTriggerFilter("duel"))
async def duel_entry(message: Message, trigger_args: TriggerArgs, osu_api_client, tenant_chat_id=None):
    """Unified ``duel`` entry-point.

    - ``duel``                 → show the duel profile panel.
    - ``duel <nick> [mode]``   → challenge that player (delegates to duel.py).
    """
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return

    if trigger_args.args and trigger_args.args.strip():
        await handle_challenge(message, trigger_args, osu_api_client, tenant_chat_id=tenant_chat_id)
        return

    tg_id = message.from_user.id
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id, tenant_chat_id)
        if not user or not user.osu_user_id:
            await message.answer("Сначала зарегистрируйтесь: <code>register &lt;nickname&gt;</code>", parse_mode="HTML")
            return
    await message.answer(
        "<b>⚔️ DUELS</b>\n\n"
        "Многораундовые 1v1 дуэли с авто-подбором карт под ваш уровень.\n"
        "Рейтинг — единый TrueSkill (μ ± σ), дивизион по conservative.\n\n"
        "Выберите режим и действие:",
        parse_mode="HTML",
        reply_markup=build_duel_panel_keyboard("casual"),
    )


@router.callback_query(F.data.startswith("duel:"))
async def duel_switch_mode(callback: CallbackQuery, tenant_chat_id=None):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    owner_tg_id = int(parts[1])
    mode = parts[2]

    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    data = await get_duel_data(owner_tg_id, mode, tenant_chat_id)
    if not data:
        await callback.answer()
        return

    img_buf = await card_renderer.generate_duel_card_async(data)
    await safe_edit_media(
        callback.message,
        media=InputMediaPhoto(media=BufferedInputFile(img_buf.read(), filename="duel.png")),
        reply_markup=build_duel_keyboard(owner_tg_id, mode),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("duelpanel:"))
async def on_duel_panel(callback: CallbackQuery, osu_api_client, tenant_chat_id=None):
    parts = callback.data.split(":")
    action = parts[1]

    if action in ("find", "challenge") and not await ensure_dm_tenant(callback, tenant_chat_id):
        return

    if action == "mode":
        mode = parts[2]
        await callback.message.edit_reply_markup(reply_markup=build_duel_panel_keyboard(mode))
        await callback.answer(f"Режим: {mode.upper()}")
        return

    if action == "find":
        mode = parts[2] if len(parts) > 2 else "casual"
        await callback.answer()
        tg_id = callback.from_user.id
        now = datetime.now(timezone.utc)

        async with get_db_session() as session:
            user = await get_any_user_by_telegram_id(session, tg_id, tenant_chat_id)
            if not user:
                await callback.message.answer("Вы не зарегистрированы.")
                return

            my_rating = (await session.execute(
                select(DuelRating).where(DuelRating.user_id == user.id, DuelRating.mode == mode)
            )).scalar_one_or_none()
            my_mu = my_rating.mu if my_rating else 2250.0

            _looking_for_duel[user.id] = (mode, now)

            stale = [uid for uid, (m, ts) in _looking_for_duel.items() if now - ts > LOOKING_TIMEOUT]
            for uid in stale:
                _looking_for_duel.pop(uid, None)

            active_ids_stmt = select(Duel.player1_user_id, Duel.player2_user_id).where(
                Duel.status.in_(["pending", "accepted", "round_active"])
            )
            active_rows = (await session.execute(active_ids_stmt)).all()
            busy_ids = {uid for row in active_rows for uid in row} | {user.id}

            looking_ids = {
                uid for uid, (m, _) in _looking_for_duel.items()
                if m == mode and uid not in busy_ids
            }

            online_cutoff = now - ONLINE_THRESHOLD
            candidates = (await session.execute(
                select(DuelRating, User)
                .join(User, User.id == DuelRating.user_id)
                .where(
                    DuelRating.mode == mode,
                    DuelRating.user_id.notin_(busy_ids),
                    User.osu_user_id.isnot(None),
                    User.last_seen_at >= online_cutoff,
                )
            )).all()

        def _sort_key(row):
            r, u = row
            in_queue = 0 if u.id in looking_ids else 1
            return (in_queue, abs(r.mu - my_mu))

        candidates.sort(key=_sort_key)

        if not candidates:
            await callback.message.answer(
                "Нет активных соперников прямо сейчас (никто не был онлайн последние 30 мин).\n"
                "Вы добавлены в очередь поиска на 15 минут — если кто-то нажмёт «Найти соперника», "
                "бот предложит им вас.",
            )
            return

        opponent_rating, opponent_user = candidates[0]
        diff = abs(opponent_rating.mu - my_mu)
        in_queue = opponent_user.id in looking_ids
        queue_tag = " 🔍 ищет дуэль" if in_queue else ""

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"⚔️ Вызвать {opponent_user.osu_username}",
                callback_data=f"duelpanel:challenge:{opponent_user.id}:{mode}",
            )
        ]])
        await callback.message.answer(
            f"<b>Найден соперник{queue_tag}!</b>\n\n"
            f"<b>{escape_html(opponent_user.osu_username)}</b>\n"
            f"μ: <code>{opponent_rating.mu:.1f}</code>  "
            f"(разница: <code>{diff:.1f}</code>)\n"
            f"W/L: <code>{opponent_rating.wins}/{opponent_rating.losses}</code>\n\n"
            f"Режим: <b>{mode.upper()}</b>",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if action == "pick":
        mode = parts[2] if len(parts) > 2 else "casual"
        await callback.answer()
        await callback.message.answer(
            f"Введите команду: <code>duel &lt;ник&gt; {mode}</code>",
            parse_mode="HTML",
        )
        return

    if action == "challenge":
        opponent_user_id = int(parts[2])
        mode = parts[3] if len(parts) > 3 else "casual"
        tg_id = callback.from_user.id

        async with get_db_session() as session:
            challenger = await get_any_user_by_telegram_id(session, tg_id, tenant_chat_id)
            if not challenger:
                await callback.answer("Вы не зарегистрированы.", show_alert=True)
                return

        await callback.answer("Создаю дуэль...")
        duel = await dm.create_duel(
            bot=callback.bot,
            chat_id=tenant_chat_id,
            challenger_id=challenger.id,
            opponent_id=opponent_user_id,
            mode=mode,
            osu_api=osu_api_client,
            thread_id=resolve_duel_thread(callback),
        )
        if not duel:
            await callback.message.answer(
                "Не удалось создать дуэль. Возможно, один из игроков уже в активной дуэли.",
            )
        return
