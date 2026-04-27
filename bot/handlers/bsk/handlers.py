from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto,
)
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.common.auth import require_registered_user
from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.bsk_rating import BskRating
from db.models.user import User
from services.bsk import duel_manager as dm

# In-memory queue: user_id -> (mode, timestamp).
# Safe without lock: dict mutations are atomic between await points in asyncio.
_looking_for_duel: dict[int, tuple[str, datetime]] = {}
LOOKING_TIMEOUT = timedelta(minutes=15)
ONLINE_THRESHOLD = timedelta(minutes=30)
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
                "mu_global": 250.0,
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
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user or not user.osu_user_id:
            await message.answer("Сначала зарегистрируйтесь: <code>register &lt;nickname&gt;</code>", parse_mode="HTML")
            return
    await message.answer(
        "<b>⚔️ BEATSKILL DUELS</b>\n\n"
        "Многораундовые дуэли с умным подбором карт.\n"
        "Рейтинг по 4 компонентам: Aim · Speed · Acc · Consistency\n\n"
        "Выберите режим и действие:",
        parse_mode="HTML",
        reply_markup=_build_duel_panel_keyboard("casual"),
    )


@router.callback_query(F.data.startswith("bsk:"))
async def bsk_switch_mode(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    owner_tg_id = int(parts[1])
    mode = parts[2]

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
            InlineKeyboardButton(text="⚔️ Вызвать игрока", callback_data=f"bskpanel:pick:{mode}"),
        ],
        [
            InlineKeyboardButton(text="❓ Как это работает?", callback_data="bskpanel:info"),
        ],
    ])


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
        back_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="← Назад", callback_data="bskpanel:back"),
        ]])
        await callback.message.answer(
            "<b>⚔️ Как работает BeatSkill?</b>\n\n"

            "<b>4 компонента скилла:</b>\n"
            "• 🎯 <b>Aim</b> — прыжки, углы, точность движения\n"
            "• ⚡ <b>Speed</b> — стримы, бёрсты, BPM-выносливость\n"
            "• 💎 <b>Accuracy</b> — точность попаданий, OD-карты\n"
            "• 🛡 <b>Consistency</b> — стабильность на длинных картах\n\n"

            "<b>BSK POINTS</b> = сумма всех четырёх компонентов.\n"
            "Каждая карта в пуле имеет свои веса — например, stream-карта "
            "сильнее влияет на Speed, а прыжковая — на Aim. После раунда "
            "обновляются только те компоненты, которые карта проверяет.\n\n"

            "<b>Пик-фаза:</b>\n"
            "Перед каждым раундом оба игрока выбирают из 6 карт. "
            "Одинаковый выбор — играется она. Разный — одна из двух случайно. "
            "На выбор даётся 60 секунд, иначе карта выбирается автоматически.\n\n"

            "<b>Победитель раунда</b> определяется по очкам:\n"
            "<code>0.4·pp + 0.3·accuracy + 0.2·combo% + 0.1·miss_penalty</code>\n\n"

            "<b>Адаптивная сложность:</b>\n"
            "Победитель раунда получает карту на 0.3★ сложнее в следующем. "
            "Если разрыв в счёте превышает 30% — сложность сбрасывается "
            "к базовой (anti-snowball).\n\n"

            "<b>Калибровка:</b>\n"
            "Первые 10 матчей — размещение с быстрым изменением рейтинга (K×6). "
            "Начальный уровень рассчитывается по вашему pp:\n"
            "1 000pp → 2★ · 5 000pp → 5.4★ · 10 000pp → 8.2★ · 15 000pp → 10★\n\n"

            "<b>ML-модель:</b>\n"
            "Каждую ночь обучается Ridge-регрессия на истории матчей. "
            "Она уточняет веса карт по паттернам (.osu) и реальным исходам, "
            "а также предсказывает победителя раунда до его начала.\n\n"

            "<b>Режимы:</b>\n"
            "• <b>Casual</b> — K=8, мягкие изменения, для практики\n"
            "• <b>Ranked</b> — K=16, официальный рейтинг\n\n"

            "<b>Отмена:</b>\n"
            "Используйте <code>bskcancel</code> чтобы выйти из любой активной дуэли.",
            parse_mode="HTML",
            reply_markup=back_kb,
        )
        return

    if action == "back":
        await callback.answer()
        await callback.message.edit_text(
            "<b>⚔️ BEATSKILL DUELS</b>\n\n"
            "Многораундовые дуэли с умным подбором карт.\n"
            "Рейтинг по 4 компонентам: Aim · Speed · Acc · Consistency\n\n"
            "Выберите режим и действие:",
            parse_mode="HTML",
            reply_markup=_build_duel_panel_keyboard("casual"),
        )
        return

    if action == "find":
        mode = parts[2] if len(parts) > 2 else "casual"
        await callback.answer()
        tg_id = callback.from_user.id
        now = datetime.now(timezone.utc)

        async with get_db_session() as session:
            user = await get_any_user_by_telegram_id(session, tg_id)
            if not user:
                await callback.message.answer("Вы не зарегистрированы.")
                return

            my_rating = (await session.execute(
                select(BskRating).where(BskRating.user_id == user.id, BskRating.mode == mode)
            )).scalar_one_or_none()
            my_mu = my_rating.mu_global if my_rating else 250.0

            # Add self to looking queue
            _looking_for_duel[user.id] = (mode, now)

            # Purge stale queue entries
            stale = [uid for uid, (m, ts) in _looking_for_duel.items() if now - ts > LOOKING_TIMEOUT]
            for uid in stale:
                _looking_for_duel.pop(uid, None)

            active_ids_stmt = select(BskDuel.player1_user_id, BskDuel.player2_user_id).where(
                BskDuel.status.in_(["pending", "accepted", "round_active"])
            )
            active_rows = (await session.execute(active_ids_stmt)).all()
            busy_ids = {uid for row in active_rows for uid in row} | {user.id}

            # Prefer players in looking queue with matching mode, then fall back to recently seen
            looking_ids = {
                uid for uid, (m, _) in _looking_for_duel.items()
                if m == mode and uid not in busy_ids
            }

            online_cutoff = now - ONLINE_THRESHOLD
            candidates = (await session.execute(
                select(BskRating, User)
                .join(User, User.id == BskRating.user_id)
                .where(
                    BskRating.mode == mode,
                    BskRating.user_id.notin_(busy_ids),
                    User.osu_user_id.isnot(None),
                    User.last_seen_at >= online_cutoff,
                )
            )).all()

        # Sort: looking-queue players first, then by mu distance
        def _sort_key(row):
            r, u = row
            in_queue = 0 if u.id in looking_ids else 1
            return (in_queue, abs(r.mu_global - my_mu))

        candidates.sort(key=_sort_key)

        if not candidates:
            await callback.message.answer(
                "Нет активных соперников прямо сейчас (никто не был онлайн последние 30 мин).\n"
                "Вы добавлены в очередь поиска на 15 минут — если кто-то нажмёт «Найти соперника», "
                "бот предложит им вас.",
            )
            return

        opponent_rating, opponent_user = candidates[0]
        diff = abs(opponent_rating.mu_global - my_mu)
        in_queue = opponent_user.id in looking_ids
        queue_tag = " 🔍 ищет дуэль" if in_queue else ""

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"⚔️ Вызвать {opponent_user.osu_username}",
                callback_data=f"bskpanel:challenge:{opponent_user.id}:{mode}",
            )
        ]])
        await callback.message.answer(
            f"<b>Найден соперник{queue_tag}!</b>\n\n"
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


@router.message(TextTriggerFilter("bskduel", "bskd"))
async def cmd_bsk_duel(message: Message, trigger_args: TriggerArgs, osu_api_client):
    """bskduel <nick> [casual|ranked] — challenge a player to a BSK duel."""
    tg_id = message.from_user.id

    # Parse args: "nick" or "nick casual" or "nick ranked"
    raw = (trigger_args.args or "").strip()
    if not raw:
        await message.answer(
            "Использование: <code>bskduel &lt;osu-ник&gt; [casual|ranked]</code>\n"
            "Пример: <code>bskduel nazeetskyyy ranked</code>",
            parse_mode="HTML",
        )
        return

    parts = raw.rsplit(None, 1)
    if len(parts) == 2 and parts[1].lower() in ("casual", "ranked"):
        osu_nick = parts[0].strip()
        mode = parts[1].lower()
    else:
        osu_nick = raw
        mode = "casual"

    async with get_db_session() as session:
        challenger = await get_any_user_by_telegram_id(session, tg_id)
        if not challenger or not challenger.osu_user_id:
            await message.answer(
                "Сначала зарегистрируйтесь: <code>register &lt;nickname&gt;</code>",
                parse_mode="HTML",
            )
            return

        opponent = await get_registered_user_by_osu(session, osu_username=osu_nick)

    if not opponent:
        await message.answer(
            f"Игрок <b>{escape_html(osu_nick)}</b> не найден в системе.\n"
            "Убедитесь, что ник указан точно и игрок зарегистрирован в боте.",
            parse_mode="HTML",
        )
        return

    if opponent.id == challenger.id:
        await message.answer("Нельзя вызвать самого себя. 🙂", parse_mode="HTML")
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
            "Не удалось создать дуэль — один из игроков уже в активной дуэли.",
            parse_mode="HTML",
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
        f"Раунд: <b>{duel.current_round}</b>  ·  Цель: <b>{duel.target_score:,} pts</b>",
        f"Счёт: <b>{int(duel.player1_total_score):,}</b> — <b>{int(duel.player2_total_score):,}</b>",
    ]

    if rnd:
        lines.append(f"\nТекущая карта: <b>{escape_html(rnd.beatmap_title or '???')}</b>")
        lines.append(f"⭐ {rnd.star_rating:.2f}  ·  https://osu.ppy.sh/b/{rnd.beatmap_id}")
        p1_done = "✅" if rnd.player1_points is not None else "⏳"
        p2_done = "✅" if rnd.player2_points is not None else "⏳"
        lines.append(f"{p1_done} {escape_html(p1_name)}  {p2_done} {escape_html(p2_name)}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("bskcancel", "bskc"))
async def cmd_bsk_cancel(message: Message):
    """Cancel your active BSK duel.
    - Pending: only the challenger can cancel.
    - Accepted / round_active: either player can cancel (forfeits the duel).
    """
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        duel = (await session.execute(
            select(BskDuel).where(
                BskDuel.status.in_(["pending", "accepted", "round_active"]),
                (BskDuel.player1_user_id == user.id) | (BskDuel.player2_user_id == user.id),
            )
        )).scalar_one_or_none()

    if not duel:
        await message.answer("У вас нет активной дуэли, которую можно отменить.")
        return

    result = await dm.cancel_duel(message.bot, duel.id, user.id)

    if result == 'cancelled':
        await message.answer("Дуэль отменена.")
    elif result == 'not_challenger':
        await message.answer("Отменить вызов может только тот, кто его отправил.")
    else:
        await message.answer("Не удалось отменить дуэль.")


@router.message(TextTriggerFilter("bskstats", "bsks"))
async def cmd_bsk_stats(message: Message):
    tg_id = message.from_user.id
    mode = "casual"
    data = await _get_bsk_data(tg_id, mode)
    if not data:
        await message.answer("Вы не зарегистрированы.")
        return
    img_buf = await card_renderer.generate_bsk_card_async(data)
    await message.answer_photo(
        BufferedInputFile(img_buf.read(), filename="bsk.png"),
        reply_markup=_build_bsk_keyboard(tg_id, mode),
    )


@router.callback_query(F.data.startswith("bskpick:"))
async def on_bsk_pick(callback: CallbackQuery):
    """Handle a player's map pick during the pick phase."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Неверный формат.", show_alert=True)
        return

    duel_id = int(parts[1])
    beatmap_id = int(parts[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return

    result = await dm.submit_pick(callback.bot, duel_id, user.id, beatmap_id)

    if result == 'ok':
        await callback.answer("✅ Выбор принят! Ждём второго игрока.", show_alert=False)
    elif result == 'done':
        await callback.answer("✅ Оба выбрали — определяем карту!", show_alert=False)
    elif result == 'already':
        await callback.answer("Вы уже сделали выбор.", show_alert=True)
    else:
        await callback.answer("Сейчас нельзя выбрать карту.", show_alert=True)


@router.callback_query(F.data.startswith("bskban:"))
async def on_bsk_ban_toggle(callback: CallbackQuery):
    """Toggle a map in the player's ban selection during the ban phase."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid format.", show_alert=True)
        return
    try:
        duel_id    = int(parts[1])
        beatmap_id = int(parts[2])
    except ValueError:
        await callback.answer("Invalid format.", show_alert=True)
        return

    result = await dm.toggle_ban(callback.bot, duel_id, callback.from_user.id, beatmap_id)

    if result == 'ok':
        await callback.answer()
    elif result == 'limit':
        await callback.answer(
            f"Максимум {dm.MAX_BANS} бана — сначала сними один.", show_alert=True
        )
    elif result == 'already_ready':
        await callback.answer("Ты уже подтвердил баны.", show_alert=True)
    else:
        await callback.answer("Фаза бана для этой дуэли не активна.", show_alert=True)


@router.callback_query(F.data.startswith("bskbandone:"))
async def on_bsk_ban_confirm(callback: CallbackQuery):
    """Confirm the player's ban selection."""
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("Неверный формат.", show_alert=True)
        return
    try:
        duel_id = int(parts[1])
    except ValueError:
        await callback.answer("Неверный формат.", show_alert=True)
        return

    result = await dm.confirm_ban(callback.bot, duel_id, callback.from_user.id)

    if result == 'done':
        await callback.answer("✅ Оба готовы — начинаем выбор карты!", show_alert=False)
    elif result == 'ok':
        await callback.answer("✅ Баны подтверждены! Ждём соперника…", show_alert=False)
    elif result == 'already':
        await callback.answer("Ты уже подтвердил баны.", show_alert=True)
    else:
        await callback.answer("Фаза бана для этой дуэли не активна.", show_alert=True)


@router.callback_query(F.data.startswith("bskd:test_cancel:"))
async def on_bskd_test_cancel(callback: CallbackQuery):
    """Cancel a test duel via inline button."""
    duel_id = int(callback.data.split(":")[-1])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return

    ok = await dm.cancel_test_duel(callback.bot, duel_id, user.id)
    if ok:
        await callback.answer("Тестовая дуэль отменена.", show_alert=False)
    else:
        await callback.answer("Нельзя отменить эту дуэль.", show_alert=True)


def _pause_keyboard(duel_id: int, is_test: bool) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="⏸ Пауза", callback_data=f"bskd:pause:{duel_id}")]
    if is_test:
        row.append(InlineKeyboardButton(text="❌ Отменить", callback_data=f"bskd:test_cancel:{duel_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _resume_keyboard(duel_id: int, is_test: bool) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"bskd:resume:{duel_id}")]
    if is_test:
        row.append(InlineKeyboardButton(text="❌ Отменить", callback_data=f"bskd:test_cancel:{duel_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


@router.callback_query(F.data.startswith("bskd:pause:"))
async def on_bskd_pause(callback: CallbackQuery):
    duel_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        is_test = duel.is_test if duel else False

    result = await dm.vote_pause(callback.bot, duel_id, user.id)
    if result == 'voted':
        await callback.answer("Вы проголосовали за паузу. Ждём второго игрока.", show_alert=True)
    elif result == 'paused':
        # Swap button to "Resume" so the player can unpause
        try:
            await callback.message.edit_reply_markup(
                reply_markup=_resume_keyboard(duel_id, is_test)
            )
        except Exception:
            pass
        await callback.answer("⏸ Пауза! Форфейт продлён на 15 минут.", show_alert=True)
    elif result == 'already':
        await callback.answer("Вы уже проголосовали за паузу.", show_alert=True)
    else:
        await callback.answer("Нельзя поставить паузу сейчас.", show_alert=True)


@router.callback_query(F.data.startswith("bskd:resume:"))
async def on_bskd_resume(callback: CallbackQuery):
    duel_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        is_test = duel.is_test if duel else False

    result = await dm.resume_duel(callback.bot, duel_id, user.id)
    if result == 'resumed':
        # Swap button back to "Pause"
        try:
            await callback.message.edit_reply_markup(
                reply_markup=_pause_keyboard(duel_id, is_test)
            )
        except Exception:
            pass
        await callback.answer("▶️ Дуэль возобновлена!", show_alert=False)
    else:
        await callback.answer("Нельзя возобновить сейчас.", show_alert=True)


@router.message(TextTriggerFilter("bskhistory", "bskh"))
async def cmd_bsk_history(message: Message, trigger_args: TriggerArgs):
    """bskhistory [N] — show last N completed duels (default 5)."""
    tg_id = message.from_user.id
    args = (trigger_args.args or "").strip()
    limit = 5
    try:
        if args:
            limit = max(1, min(int(args), 20))
    except ValueError:
        pass

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        duels = (await session.execute(
            select(BskDuel).where(
                BskDuel.status == 'completed',
                BskDuel.is_test == False,
                (BskDuel.player1_user_id == user.id) | (BskDuel.player2_user_id == user.id),
            ).order_by(BskDuel.completed_at.desc()).limit(limit)
        )).scalars().all()

        if not duels:
            await message.answer("У вас ещё нет завершённых дуэлей.")
            return

        # Fetch all opponent user ids
        opponent_ids = {
            d.player2_user_id if d.player1_user_id == user.id else d.player1_user_id
            for d in duels
        }
        users_raw = (await session.execute(
            select(User).where(User.id.in_(opponent_ids | {user.id}))
        )).scalars().all()
        users_map = {u.id: u for u in users_raw}

    lines = [f"<b>История дуэлей</b> (последние {len(duels)}):\n"]
    for d in duels:
        is_p1 = d.player1_user_id == user.id
        opp_id = d.player2_user_id if is_p1 else d.player1_user_id
        opp = users_map.get(opp_id)
        opp_name = escape_html(opp.osu_username) if opp else "???"

        my_score = int(d.player1_total_score) if is_p1 else int(d.player2_total_score)
        opp_score = int(d.player2_total_score) if is_p1 else int(d.player1_total_score)

        won = d.winner_user_id == user.id
        draw = d.winner_user_id is None
        icon = "🤝" if draw else ("✅" if won else "❌")

        date = d.completed_at.strftime("%d.%m") if d.completed_at else "?"
        lines.append(
            f"{icon} <b>{opp_name}</b> [{d.mode}] {date}\n"
            f"   <code>{my_score:,}</code> — <code>{opp_score:,}</code>"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")
