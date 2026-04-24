from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto,
)
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.common.auth import require_registered_user
from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.bsk_rating import BskRating
from db.models.user import User
from services.bsk import duel_manager as dm
from services.image import card_renderer
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.osu.resolve_user import get_any_user_by_telegram_id, get_registered_user_by_osu, resolve_osu_user

router = Router(name="bsk")
logger = get_logger("handlers.bsk")


def _build_bsk_keyboard(tg_id: int, active_mode: str) -> InlineKeyboardMarkup:
    modes = [("casual", "Casual"), ("ranked", "Ranked")]
    buttons = []
    for mode, label in modes:
        text = f"• {label} •" if mode == active_mode else label
        buttons.append(InlineKeyboardButton(
            text=text,
            callback_data=f"bsk:{tg_id}:{mode}",
        ))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


async def _get_bsk_rank(session, user_id: int, mode: str, mu_global: float) -> int | None:
    all_stmt = select(BskRating).where(BskRating.mode == mode)
    all_ratings = (await session.execute(all_stmt)).scalars().all()
    if not all_ratings:
        return None
    rank = 1 + sum(1 for r in all_ratings if r.user_id != user_id and r.mu_global > mu_global)
    return rank


async def _get_bsk_data(tg_id: int, mode: str) -> dict | None:
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user or not user.osu_user_id:
            return None

        cover_data = user.cover_data

        rating_stmt = select(BskRating).where(
            BskRating.user_id == user.id,
            BskRating.mode == mode,
        )
        rating = (await session.execute(rating_stmt)).scalar_one_or_none()

        base = {
            "username": user.osu_username,
            "country": user.country or "",
            "avatar_url": user.avatar_url,
            "cover_data": bytes(cover_data) if cover_data else None,
            "mode": mode,
        }

        if not rating:
            return {
                **base,
                "mu_global": 1000.0,
                "mu_aim": 250.0,
                "mu_speed": 250.0,
                "mu_acc": 250.0,
                "mu_cons": 250.0,
                "peak_mu": 1000.0,
                "wins": 0,
                "losses": 0,
                "placement_matches_left": 10,
                "bsk_rank": None,
            }

        bsk_rank = await _get_bsk_rank(session, user.id, mode, rating.mu_global)

        return {
            **base,
            "mu_global": rating.mu_global,
            "mu_aim": rating.mu_aim,
            "mu_speed": rating.mu_speed,
            "mu_acc": rating.mu_acc,
            "mu_cons": rating.mu_cons,
            "peak_mu": rating.peak_mu,
            "wins": rating.wins,
            "losses": rating.losses,
            "placement_matches_left": rating.placement_matches_left,
            "bsk_rank": bsk_rank,
        }


@router.message(TextTriggerFilter("bsk"))
async def bsk_profile(message: Message, trigger_args: TriggerArgs):
    tg_id = message.from_user.id
    mode = "casual"

    data = await _get_bsk_data(tg_id, mode)
    if not data:
        await message.answer("Сначала зарегистрируйтесь: <code>register &lt;nickname&gt;</code>", parse_mode="HTML")
        return

    img_buf = await card_renderer.generate_bsk_card_async(data)
    await message.answer_photo(
        BufferedInputFile(img_buf.read(), filename="bsk.png"),
        reply_markup=_build_bsk_keyboard(tg_id, mode),
    )


@router.callback_query(F.data.startswith("bsk:"))
async def bsk_switch_mode(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    owner_tg_id = int(parts[1])
    mode = parts[2]

    if callback.from_user.id != owner_tg_id:
        await callback.answer("Это не ваша карточка.", show_alert=True)
        return

    data = await _get_bsk_data(owner_tg_id, mode)
    if not data:
        await callback.answer()
        return

    img_buf = await card_renderer.generate_bsk_card_async(data)
    await callback.message.edit_media(
        InputMediaPhoto(media=BufferedInputFile(img_buf.read(), filename="bsk.png")),
        reply_markup=_build_bsk_keyboard(owner_tg_id, mode),
    )
    await callback.answer()


# ─── BSK Duel Panel ───────────────────────────────────────────────────────────

def _build_duel_panel_keyboard(mode: str = "casual") -> InlineKeyboardMarkup:
    mode_casual = "• Casual •" if mode == "casual" else "Casual"
    mode_ranked = "• Ranked •" if mode == "ranked" else "Ranked"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=mode_casual, callback_data="bskpanel:mode:casual"),
            InlineKeyboardButton(text=mode_ranked, callback_data="bskpanel:mode:ranked"),
        ],
        [
            InlineKeyboardButton(text="🔍 Найти соперника", callback_data=f"bskpanel:find:{mode}"),
            InlineKeyboardButton(text="⚔️ Выбрать соперника", callback_data=f"bskpanel:pick:{mode}"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="bskpanel:stats"),
            InlineKeyboardButton(text="❓ Как это работает?", callback_data="bskpanel:info"),
        ],
    ])


@router.message(TextTriggerFilter("bskpanel", "bskmenu"))
async def cmd_bsk_panel(message: Message):
    tg_id = message.from_user.id
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user or not user.osu_user_id:
            await message.answer(
                "Сначала зарегистрируйтесь: <code>register &lt;nickname&gt;</code>",
                parse_mode="HTML",
            )
            return

    await message.answer(
        "<b>⚔️ BEATSKILL DUELS</b>\n\n"
        "Многораундовые дуэли с умным подбором карт.\n"
        "Рейтинг по 4 компонентам: Aim · Speed · Acc · Consistency\n\n"
        "Выберите режим и действие:",
        parse_mode="HTML",
        reply_markup=_build_duel_panel_keyboard("casual"),
    )


@router.callback_query(F.data.startswith("bskpanel:"))
async def on_bsk_panel(callback: CallbackQuery, osu_api_client):
    parts = callback.data.split(":")
    action = parts[1]

    if action == "mode":
        mode = parts[2]
        await callback.message.edit_reply_markup(reply_markup=_build_duel_panel_keyboard(mode))
        await callback.answer(f"Режим: {mode.upper()}")
        return

    if action == "info":
        await callback.answer()
        await callback.message.answer(
            "<b>Как работает BeatSkill?</b>\n\n"
            "<b>Рейтинг (4 компоненты):</b>\n"
            "• <b>Aim</b> — точность прицеливания на jump-картах\n"
            "• <b>Speed</b> — скорость на stream-картах\n"
            "• <b>Accuracy</b> — точность попаданий на технических картах\n"
            "• <b>Consistency</b> — стабильность на длинных картах\n\n"
            "<b>Победитель раунда</b> определяется по composite-очкам:\n"
            "<code>0.4·pp + 0.3·accuracy + 0.2·combo% + 0.1·miss_penalty</code>\n\n"
            "<b>Подбор карт:</b>\n"
            "Бот смотрит на ваш уровень (μ_global) и выбирает карту "
            "соответствующей сложности из пула. Победитель раунда получает "
            "карту на 0.3★ сложнее в следующем раунде. Если разрыв в счёте "
            "превышает 30% — сложность сбрасывается к базовой (anti-snowball).\n\n"
            "<b>Обновление рейтинга:</b>\n"
            "После дуэли μ каждой компоненты обновляется пропорционально "
            "весу карты для этого навыка. Aim-карта сильнее меняет μ_aim, "
            "stream-карта — μ_speed и т.д.\n\n"
            "<b>Режимы:</b>\n"
            "• <b>Casual</b> — K=8, мягкие изменения, для практики\n"
            "• <b>Ranked</b> — K=16, официальный рейтинг, 10 placement матчей",
            parse_mode="HTML",
        )
        return

    if action == "stats":
        await callback.answer()
        tg_id = callback.from_user.id
        async with get_db_session() as session:
            user = await get_any_user_by_telegram_id(session, tg_id)
            if not user:
                await callback.message.answer("Вы не зарегистрированы.")
                return

            casual = (await session.execute(
                select(BskRating).where(BskRating.user_id == user.id, BskRating.mode == "casual")
            )).scalar_one_or_none()
            ranked = (await session.execute(
                select(BskRating).where(BskRating.user_id == user.id, BskRating.mode == "ranked")
            )).scalar_one_or_none()

        def _fmt(r, mode_name: str) -> str:
            if not r:
                return f"<b>{mode_name.upper()}</b>: нет матчей"
            placement = f" ({r.placement_matches_left} placement осталось)" if r.placement_matches_left > 0 else ""
            return (
                f"<b>{mode_name.upper()}</b>{placement}\n"
                f"  μ global: <code>{r.mu_global:.1f}</code>  peak: <code>{r.peak_mu:.1f}</code>\n"
                f"  Aim: <code>{r.mu_aim:.1f}</code>  Speed: <code>{r.mu_speed:.1f}</code>  "
                f"Acc: <code>{r.mu_acc:.1f}</code>  Cons: <code>{r.mu_cons:.1f}</code>\n"
                f"  W/L: <code>{r.wins}/{r.losses}</code>"
            )

        await callback.message.answer(
            f"<b>📊 BSK Статистика — {escape_html(user.osu_username)}</b>\n\n"
            f"{_fmt(casual, 'casual')}\n\n"
            f"{_fmt(ranked, 'ranked')}",
            parse_mode="HTML",
        )
        return

    if action == "find":
        mode = parts[2] if len(parts) > 2 else "casual"
        await callback.answer()
        tg_id = callback.from_user.id
        async with get_db_session() as session:
            user = await get_any_user_by_telegram_id(session, tg_id)
            if not user:
                await callback.message.answer("Вы не зарегистрированы.")
                return

            my_rating = (await session.execute(
                select(BskRating).where(BskRating.user_id == user.id, BskRating.mode == mode)
            )).scalar_one_or_none()
            my_mu = my_rating.mu_global if my_rating else 1000.0

            active_ids_stmt = select(BskDuel.player1_user_id, BskDuel.player2_user_id).where(
                BskDuel.status.in_(["pending", "accepted", "round_active"])
            )
            active_rows = (await session.execute(active_ids_stmt)).all()
            busy_ids = {uid for row in active_rows for uid in row} | {user.id}

            candidates = (await session.execute(
                select(BskRating, User)
                .join(User, User.id == BskRating.user_id)
                .where(
                    BskRating.mode == mode,
                    BskRating.user_id.notin_(busy_ids),
                    User.osu_user_id.isnot(None),
                )
            )).all()

        if not candidates:
            await callback.message.answer(
                "Нет доступных соперников прямо сейчас. Попробуйте позже или вызовите конкретного игрока.",
            )
            return

        best = min(candidates, key=lambda row: abs(row[0].mu_global - my_mu))
        opponent_rating, opponent_user = best
        diff = abs(opponent_rating.mu_global - my_mu)

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"⚔️ Вызвать {opponent_user.osu_username}",
                callback_data=f"bskpanel:challenge:{opponent_user.id}:{mode}",
            )
        ]])
        await callback.message.answer(
            f"<b>Найден соперник!</b>\n\n"
            f"<b>{escape_html(opponent_user.osu_username)}</b>\n"
            f"μ: <code>{opponent_rating.mu_global:.1f}</code>  "
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
            f"Введите команду: <code>bskduel &lt;ник&gt; {mode}</code>",
            parse_mode="HTML",
        )
        return

    if action == "challenge":
        opponent_user_id = int(parts[2])
        mode = parts[3] if len(parts) > 3 else "casual"
        tg_id = callback.from_user.id

        async with get_db_session() as session:
            challenger = await get_any_user_by_telegram_id(session, tg_id)
            if not challenger:
                await callback.answer("Вы не зарегистрированы.", show_alert=True)
                return

        await callback.answer("Создаю дуэль...")
        duel = await dm.create_duel(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            challenger_id=challenger.id,
            opponent_id=opponent_user_id,
            mode=mode,
            osu_api=osu_api_client,
        )
        if not duel:
            await callback.message.answer(
                "Не удалось создать дуэль. Возможно, один из игроков уже в активной дуэли.",
            )
        return

    await callback.answer()


@router.message(TextTriggerFilter("bskduel", "bskd"))
async def cmd_bsk_duel(message: Message, trigger_args: TriggerArgs, osu_api_client):
    """bskduel <ник> [casual|ranked]"""
    tg_id = message.from_user.id
    args = (trigger_args.args or "").strip().split()

    if not args:
        await message.answer(
            "<b>BSK Дуэль</b>\n\n"
            "Использование: <code>bskduel &lt;ник&gt; [casual|ranked]</code>\n\n"
            "Режимы:\n"
            "• <b>casual</b> — K=8, без влияния на ranked рейтинг\n"
            "• <b>ranked</b> — K=16, placement матчи, официальный рейтинг\n\n"
            "5 раундов, карты подбираются по вашему уровню.\n"
            "Победитель определяется по сумме composite-очков.",
            parse_mode="HTML",
        )
        return

    target_name = args[0].lstrip("@")
    mode = "casual"
    if len(args) > 1 and args[1].lower() in ("casual", "ranked"):
        mode = args[1].lower()

    async with get_db_session() as session:
        challenger = await require_registered_user(session, message=message)
        if not challenger:
            return

        # Resolve target
        user_data = await resolve_osu_user(osu_api_client, target_name)
        if not user_data:
            await message.answer(
                f"Игрок <b>{escape_html(target_name)}</b> не найден в osu!.",
                parse_mode="HTML",
            )
            return

        opponent = await get_registered_user_by_osu(
            session,
            osu_user_id=user_data.get("id"),
            osu_username=user_data.get("username"),
        )
        if not opponent:
            await message.answer(
                f"Игрок <b>{escape_html(user_data['username'])}</b> найден в osu!, но не зарегистрирован в боте.",
                parse_mode="HTML",
            )
            return

        if opponent.id == challenger.id:
            await message.answer("Нельзя вызвать самого себя!")
            return

    duel = await dm.create_duel(
        bot=message.bot,
        chat_id=message.chat.id,
        challenger_id=challenger.id,
        opponent_id=opponent.id,
        mode=mode,
        osu_api=osu_api_client,
    )

    if not duel:
        await message.answer(
            "Не удалось создать дуэль. Возможно, один из игроков уже в активной дуэли.",
        )


@router.callback_query(F.data.startswith("bskd:accept:"))
async def on_bskd_accept(callback: CallbackQuery, osu_api_client):
    duel_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Сначала зарегистрируйтесь.", show_alert=True)
            return

        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()

        if not duel:
            await callback.answer("Дуэль не найдена.", show_alert=True)
            return
        if duel.player2_user_id != user.id:
            await callback.answer("Этот вызов не для вас!", show_alert=True)
            return

    await callback.answer("Принимаю дуэль...")
    ok = await dm.accept_duel(callback.bot, duel_id, user.id, osu_api_client)
    if not ok:
        await callback.answer("Не удалось принять дуэль (истекла или уже принята).", show_alert=True)


@router.callback_query(F.data.startswith("bskd:decline:"))
async def on_bskd_decline(callback: CallbackQuery):
    duel_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Сначала зарегистрируйтесь.", show_alert=True)
            return

    await callback.answer("Отклоняю...")
    ok = await dm.decline_duel(callback.bot, duel_id, user.id)
    if not ok:
        await callback.answer("Не удалось отклонить дуэль.", show_alert=True)


@router.message(TextTriggerFilter("bskstatus", "bskst"))
async def cmd_bsk_status(message: Message):
    """Show current active BSK duel status."""
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        duel = (await session.execute(
            select(BskDuel).where(
                BskDuel.status.in_(["pending", "accepted", "round_active"]),
                (
                    (BskDuel.player1_user_id == user.id) |
                    (BskDuel.player2_user_id == user.id)
                ),
            )
        )).scalar_one_or_none()

        if not duel:
            await message.answer("У вас нет активной BSK дуэли.")
            return

        p1 = (await session.execute(select(User).where(User.id == duel.player1_user_id))).scalar_one_or_none()
        p2 = (await session.execute(select(User).where(User.id == duel.player2_user_id))).scalar_one_or_none()

        rnd = None
        if duel.status == "round_active":
            rnd = (await session.execute(
                select(BskDuelRound)
                .where(BskDuelRound.duel_id == duel.id, BskDuelRound.status == "waiting")
                .order_by(BskDuelRound.round_number.desc())
            )).scalar_one_or_none()

    p1_name = p1.osu_username if p1 else "???"
    p2_name = p2.osu_username if p2 else "???"

    lines = [
        f"<b>BSK Дуэль</b> — {escape_html(p1_name)} vs {escape_html(p2_name)}",
        f"Режим: <b>{duel.mode.upper()}</b>  ·  Статус: <b>{duel.status}</b>",
        f"Раунд: <b>{duel.current_round}/{duel.total_rounds}</b>",
        f"Счёт: <b>{duel.player1_total_score:.3f}</b> — <b>{duel.player2_total_score:.3f}</b>",
    ]

    if rnd:
        lines.append(f"\nТекущая карта: <b>{escape_html(rnd.beatmap_title or '???')}</b>")
        lines.append(f"⭐ {rnd.star_rating:.2f}  ·  https://osu.ppy.sh/b/{rnd.beatmap_id}")
        p1_done = "✅" if rnd.player1_composite is not None else "⏳"
        p2_done = "✅" if rnd.player2_composite is not None else "⏳"
        lines.append(f"{p1_done} {escape_html(p1_name)}  {p2_done} {escape_html(p2_name)}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("bskcancel", "bskc"))
async def cmd_bsk_cancel(message: Message):
    """Cancel your active BSK duel (only pending duels can be cancelled by challenger)."""
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        duel = (await session.execute(
            select(BskDuel).where(
                BskDuel.status == "pending",
                BskDuel.player1_user_id == user.id,
            )
        )).scalar_one_or_none()

        if not duel:
            await message.answer("Нет активного вызова, который можно отменить.")
            return

        duel.status = "cancelled"
        await session.commit()

        try:
            await message.bot.edit_message_text(
                "❌ Вызов отменён инициатором.",
                chat_id=duel.chat_id,
                message_id=duel.message_id,
                parse_mode="HTML",
            )
        except Exception:
            pass

    await message.answer("Вызов отменён.")



def _build_bsk_keyboard(tg_id: int, active_mode: str) -> InlineKeyboardMarkup:
    modes = [("casual", "Casual"), ("ranked", "Ranked")]
    buttons = []
    for mode, label in modes:
        text = f"• {label} •" if mode == active_mode else label
        buttons.append(InlineKeyboardButton(
            text=text,
            callback_data=f"bsk:{tg_id}:{mode}",
        ))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


async def _get_bsk_rank(session, user_id: int, mode: str, mu_global: float) -> int | None:
    all_stmt = select(BskRating).where(BskRating.mode == mode)
    all_ratings = (await session.execute(all_stmt)).scalars().all()
    if not all_ratings:
        return None
    rank = 1 + sum(1 for r in all_ratings if r.user_id != user_id and r.mu_global > mu_global)
    return rank


async def _get_bsk_data(tg_id: int, mode: str) -> dict | None:
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user or not user.osu_user_id:
            return None

        # Read cover_data explicitly inside session
        cover_data = user.cover_data

        rating_stmt = select(BskRating).where(
            BskRating.user_id == user.id,
            BskRating.mode == mode,
        )
        rating = (await session.execute(rating_stmt)).scalar_one_or_none()

        base = {
            "username": user.osu_username,
            "country": user.country or "",
            "avatar_url": user.avatar_url,
            "cover_data": bytes(cover_data) if cover_data else None,
            "mode": mode,
        }

        if not rating:
            return {
                **base,
                "mu_global": 1000.0,
                "mu_aim": 250.0,
                "mu_speed": 250.0,
                "mu_acc": 250.0,
                "mu_cons": 250.0,
                "peak_mu": 1000.0,
                "wins": 0,
                "losses": 0,
                "placement_matches_left": 10,
                "bsk_rank": None,
            }

        bsk_rank = await _get_bsk_rank(session, user.id, mode, rating.mu_global)

        return {
            **base,
            "mu_global": rating.mu_global,
            "mu_aim": rating.mu_aim,
            "mu_speed": rating.mu_speed,
            "mu_acc": rating.mu_acc,
            "mu_cons": rating.mu_cons,
            "peak_mu": rating.peak_mu,
            "wins": rating.wins,
            "losses": rating.losses,
            "placement_matches_left": rating.placement_matches_left,
            "bsk_rank": bsk_rank,
        }


@router.message(TextTriggerFilter("bsk"))
async def bsk_profile(message: Message, trigger_args: TriggerArgs):
    tg_id = message.from_user.id
    mode = "casual"

    data = await _get_bsk_data(tg_id, mode)
    if not data:
        await message.answer("Сначала зарегистрируйтесь: <code>register &lt;nickname&gt;</code>", parse_mode="HTML")
        return

    img_buf = await card_renderer.generate_bsk_card_async(data)
    await message.answer_photo(
        BufferedInputFile(img_buf.read(), filename="bsk.png"),
        reply_markup=_build_bsk_keyboard(tg_id, mode),
    )


@router.callback_query(F.data.startswith("bsk:"))
async def bsk_switch_mode(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    owner_tg_id = int(parts[1])
    mode = parts[2]

    if callback.from_user.id != owner_tg_id:
        await callback.answer("Это не ваша карточка.", show_alert=True)
        return

    data = await _get_bsk_data(owner_tg_id, mode)
    if not data:
        await callback.answer()
        return

    img_buf = await card_renderer.generate_bsk_card_async(data)
    await callback.message.edit_media(
        InputMediaPhoto(media=BufferedInputFile(img_buf.read(), filename="bsk.png")),
        reply_markup=_build_bsk_keyboard(owner_tg_id, mode),
    )
    await callback.answer()
