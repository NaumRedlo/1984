"""
Telegram handlers for the simplified duel system.

Players create their own multiplayer room and play.
Bot picks the map, then checks recent scores via API.

Triggers: duel, duelhistory/dh, duelresult/dr, duelstats/ds, duelcancel/dc
Callbacks: duel_accept, duel_decline, duel_pick, duel_custom
"""

from aiogram import Router, types, F, Bot
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from db.database import get_db_session
from db.models.duel import Duel
from db.models.user import User
from services.image import card_renderer
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_registered_user, resolve_osu_query_status
from bot.filters import TextTriggerFilter, TriggerArgs

router = Router(name="duel")
logger = get_logger("handlers.duel")

_duel_manager = None
_bot_instance: Bot = None


def init_duel_handlers(duel_manager, bot: Bot):
    """Called from main.py to inject dependencies."""
    global _duel_manager, _bot_instance
    _duel_manager = duel_manager
    _bot_instance = bot
    duel_manager.set_telegram_callback(_handle_timeout_event)


# FSM for custom beatmap ID input

class DuelPickStates(StatesGroup):
    waiting_beatmap_id = State()


# Commands

@router.message(TextTriggerFilter("duel"))
async def cmd_duel(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """Challenge someone: duel username [bo3/bo5/bo7]"""
    if not _duel_manager:
        await message.answer("Система дуэлей недоступна.")
        return

    args_text = trigger_args.args
    if not args_text:
        await message.answer(
            "<b>СИСТЕМА ДУЭЛЕЙ</b>\n\n"
            "Вызови другого игрока на дуэль Best-of-N!\n"
            "Вы играете карты, бот проверяет скоры.\n\n"
            "Использование: <code>duel ник [bo3/bo5/bo7]</code>\n"
            "История дуэлей: <code>duelhistory</code> или <code>dh</code>\n"
            "Проверить результат: <code>duelresult</code> или <code>dr</code>\n"
            "Статистика: <code>duelstats</code> или <code>ds</code>\n"
            "Отменить дуэль: <code>duelcancel</code> или <code>dc</code>",
            parse_mode="HTML",
        )
        return

    parts = args_text.strip().split()
    target_name = parts[0].lstrip("@")

    best_of = 5
    if len(parts) > 1:
        bo_str = parts[1].lower()
        if bo_str in ("bo3", "3"):
            best_of = 3
        elif bo_str in ("bo5", "5"):
            best_of = 5
        elif bo_str in ("bo7", "7"):
            best_of = 7

    challenger_tg_id = message.from_user.id

    async with get_db_session() as session:
        challenger = await get_registered_user(session, challenger_tg_id)
        if not challenger:
            await message.answer(
                "Сначала зарегистрируйся! Используй <code>register</code>",
                parse_mode="HTML",
            )
            return

        target, status = await _resolve_target(session, osu_api_client, target_name)
        if not target:
            if status == "not_found":
                await message.answer(
                    f"Игрок <b>{escape_html(target_name)}</b> не найден в базе osu!.",
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    f"Игрок <b>{escape_html(target_name)}</b> найден в osu!, но не зарегистрирован в боте.",
                    parse_mode="HTML",
                )
            return

        if target.telegram_id == challenger_tg_id:
            await message.answer("Нельзя вызвать самого себя!")
            return

    duel = await _duel_manager.create_duel(
        player1_tg_id=challenger_tg_id,
        player2_tg_id=target.telegram_id,
        best_of=best_of,
        chat_id=message.chat.id,
    )

    if not duel:
        await message.answer("Не удалось создать дуэль. Возможно, кто-то из вас уже в активной дуэли.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Принять", callback_data=f"duel_accept:{duel.id}"),
            InlineKeyboardButton(text="Отклонить", callback_data=f"duel_decline:{duel.id}"),
        ]
    ])

    async with get_db_session() as session:
        p1 = await session.get(User, duel.player1_user_id)
        p2 = await session.get(User, duel.player2_user_id)
        p1_name = p1.osu_username if p1 else "???"
        p2_name = p2.osu_username if p2 else "???"

    p2_tg_mention = f"<a href=\"tg://user?id={p2.telegram_id}\">{escape_html(p2_name)}</a>" if p2 and p2.telegram_id else escape_html(p2_name)
    await message.answer(
        f"<b>ВЫЗОВ НА ДУЭЛЬ</b>\n\n"
        f"<b>{escape_html(p1_name)}</b> vs <b>{escape_html(p2_name)}</b>\n"
        f"Формат: Best of {best_of}\n\n"
        f"<i>{p2_tg_mention}, принимаешь вызов?</i>",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.message(TextTriggerFilter("duelresult", "dr"))
async def cmd_duelresult(message: types.Message, trigger_args: TriggerArgs):
    """Check recent scores and determine round winner."""
    if not _duel_manager:
        await message.answer("Система дуэлей недоступна.")
        return

    tg_id = message.from_user.id
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id)
        if not user:
            await message.answer(
                "Сначала зарегистрируйся! Используй <code>register</code>",
                parse_mode="HTML",
            )
            return

    state = _duel_manager.find_user_duel(user.id)
    if not state:
        await message.answer("У тебя нет активной дуэли.")
        return

    if not state.current_beatmap_id:
        await message.answer("Карта ещё не выбрана. Сначала выбери карту!")
        return

    wait_msg = await message.answer("Проверяю скоры...")

    result = await _duel_manager.check_results(state.duel_id)

    if not result:
        await wait_msg.edit_text(
            "Скоры не найдены. Оба игрока должны сыграть карту!\n"
            f"Карта: https://osu.ppy.sh/b/{state.current_beatmap_id}"
        )
        return

    # Both must play — show who's missing
    if result.get("waiting"):
        p1_status = "\u2705" if result["p1_played"] else "\u274c"
        p2_status = "\u2705" if result["p2_played"] else "\u274c"
        time_left = ""
        if state.round_picked_at:
            from datetime import datetime, timezone
            elapsed = (datetime.now(timezone.utc) - state.round_picked_at).total_seconds()
            remaining = max(0, 900 - elapsed)  # 15 min timeout
            mins = int(remaining // 60)
            time_left = f"\n\nОсталось времени: <b>{mins} мин</b>"
        await wait_msg.edit_text(
            f"<b>Ожидание обоих игроков...</b>\n\n"
            f"{p1_status} {escape_html(result['player1_name'])}\n"
            f"{p2_status} {escape_html(result['player2_name'])}\n"
            f"Карта: https://osu.ppy.sh/b/{state.current_beatmap_id}"
            f"{time_left}\n\n"
            f"<i>Через 15 мин тот, кто не сыграл, проигрывает раунд.</i>",
            parse_mode="HTML",
        )
        return

    # Send round result card
    try:
        photo = await card_renderer.generate_duel_round_card_async(result)
        await message.answer_photo(
            photo=BufferedInputFile(photo.read(), filename="duel_round.png"),
        )
    except Exception as e:
        logger.error(f"Failed to generate round card: {e}")
        winner_name = result["player1_name"] if result["round_winner"] == 1 else \
                      result["player2_name"] if result["round_winner"] == 2 else "Ничья"
        await message.answer(
            f"<b>Раунд {result['round_number']} — {escape_html(winner_name)} побеждает!</b>\n"
            f"Счёт: {result['player1_wins']} — {result['player2_wins']}",
            parse_mode="HTML",
        )

    await wait_msg.delete()

    # Check if duel finished
    if result.get("finished"):
        await _send_final_result(message, state)
    else:
        # Suggest maps for next round
        suggestions = _duel_manager.suggest_maps(state.duel_id)
        pick_turn = _duel_manager._pick_turn_name(state.duel_id)
        pick_cards = _duel_manager.build_map_pick_cards(state.duel_id, suggestions)
        keyboard = _make_suggestions_keyboard(state.duel_id, suggestions)
        try:
            photo = await card_renderer.generate_duel_pick_card_async({
                "round_number": state.current_round + 1,
                "pick_turn": pick_turn,
                "suggestions": pick_cards,
            })
            await message.answer_photo(
                photo=BufferedInputFile(photo.read(), filename="duel_pick.png"),
                caption=(
                    f"Счёт: <b>{result['player1_wins']} — {result['player2_wins']}</b>\n"
                    f"Сейчас выбирает: <b>{escape_html(pick_turn)}</b>"
                ),
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            await message.answer(
                f"Счёт: <b>{result['player1_wins']} — {result['player2_wins']}</b>\n"
                f"Выбери карту на следующий раунд — ходит <b>{escape_html(pick_turn)}</b>",
                reply_markup=keyboard,
                parse_mode="HTML",
            )


@router.message(TextTriggerFilter("duelhistory", "dh"))
async def cmd_duelhistory(message: types.Message, trigger_args: TriggerArgs):
    """Show recent completed duel history."""
    if not _duel_manager:
        await message.answer("Система дуэлей недоступна.")
        return

    tg_id = message.from_user.id
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id)
        if not user:
            await message.answer(
                "Сначала зарегистрируйся! Используй <code>register</code>",
                parse_mode="HTML",
            )
            return

        duels = await _duel_manager.get_completed_duel_history(user.id, limit=5)
        if not duels:
            await message.answer("You don't have any completed duels yet.")
            return

        try:
            photo = await card_renderer.generate_duel_history_card_async({"duels": duels})
            await message.answer_photo(photo=BufferedInputFile(photo.read(), filename="duel_history.png"))
        except Exception as e:
            logger.warning(f"Failed to generate duel history card: {e}")
            lines = [f"<b>DUEL HISTORY — {escape_html(user.osu_username)}</b>"]
            for duel in duels:
                lines.append(
                    f"• <b>{escape_html(duel['opponent_name'])}</b> — {escape_html(duel['result'])} | BO{duel['best_of']} | {escape_html(str(duel['completed_at']))} | {escape_html(duel['score_line'])}"
                )
            await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("duelstats", "ds"))
async def cmd_duelstats(message: types.Message, trigger_args: TriggerArgs):
    """Show duel W/L stats."""
    tg_id = message.from_user.id
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id)
        if not user:
            await message.answer(
                "Сначала зарегистрируйся! Используй <code>register</code>",
                parse_mode="HTML",
            )
            return

        stats = await _duel_manager.get_completed_duel_stats(user.id, limit=5)
        summary = stats.get("summary", {})

        try:
            photo = await card_renderer.generate_duel_stats_card_async(stats)
            await message.answer_photo(photo=BufferedInputFile(photo.read(), filename="duel_stats.png"))
        except Exception as e:
            logger.warning(f"Failed to generate duel stats card: {e}")
            wins = summary.get("wins", user.duel_wins or 0)
            losses = summary.get("losses", user.duel_losses or 0)
            draws = summary.get("draws", 0)
            total = summary.get("total", wins + losses + draws)
            winrate = summary.get("win_rate")
            winrate_text = f"{winrate:.1f}%" if winrate is not None else "—"
            formats = ", ".join(summary.get("formats", [])) or "—"
            lines = [
                f"<b>Статистика дуэлей — {escape_html(user.osu_username)}</b>",
                f"Победы: <b>{wins:,}</b>",
                f"Поражения: <b>{losses:,}</b>",
                f"Ничьи: <b>{draws:,}</b>",
                f"Всего: <b>{total:,}</b>",
                f"Винрейт: <b>{winrate_text}</b>",
                f"Форматы: <b>{escape_html(formats)}</b>",
            ]
            if stats.get("duels"):
                lines.append("")
                lines.append("<b>Последние дуэли</b>")
                for duel in stats["duels"]:
                    lines.append(
                        f"• <b>{escape_html(duel['opponent_name'])}</b> — {escape_html(duel['result'])} | BO{duel['best_of']} | {escape_html(str(duel['completed_at']))} | {escape_html(duel['score_line'])}"
                    )
            await message.answer("\n".join(lines), parse_mode="HTML")
            return

        return


@router.message(TextTriggerFilter("duelcancel", "dc"))
async def cmd_duelcancel(message: types.Message, trigger_args: TriggerArgs):
    """Cancel your active duel."""
    if not _duel_manager:
        await message.answer("Система дуэлей недоступна.")
        return

    tg_id = message.from_user.id
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id)
        if not user:
            await message.answer(
                "Сначала зарегистрируйся! Используй <code>register</code>",
                parse_mode="HTML",
            )
            return

    state = _duel_manager.find_user_duel(user.id)
    if state:
        await _duel_manager.cancel_duel(state.duel_id, reason="cancelled by player")
        await message.answer("Дуэль отменена.")
    else:
        await message.answer("У тебя нет активной дуэли.")


# Callbacks

@router.callback_query(F.data.startswith("duel_accept:"))
async def on_duel_accept(callback: CallbackQuery):
    if not _duel_manager:
        await callback.answer("Система дуэлей недоступна", show_alert=True)
        return

    duel_id = int(callback.data.split(":")[1])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        duel = await session.get(Duel, duel_id)
        if not duel or duel.status != "pending":
            await callback.answer("Эта дуэль уже недоступна.", show_alert=True)
            return

        p2 = await session.get(User, duel.player2_user_id)
        if not p2 or p2.telegram_id != tg_id:
            await callback.answer("Этот вызов не для тебя!", show_alert=True)
            return

    await callback.answer("Принимаю дуэль...")

    state = await _duel_manager.accept_duel(duel_id)
    if not state:
        await callback.message.edit_text(
            callback.message.text + "\n\n<b>Не удалось начать дуэль.</b> Недостаточно карт в профилях.",
            parse_mode="HTML",
        )
        return

    # Show first map suggestions
    suggestions = _duel_manager.suggest_maps(duel_id)
    pick_turn = _duel_manager._pick_turn_name(duel_id)
    pick_cards = _duel_manager.build_map_pick_cards(duel_id, suggestions)
    keyboard = _make_suggestions_keyboard(duel_id, suggestions)

    await callback.message.edit_text(
        f"<b>ДУЭЛЬ ПРИНЯТА!</b>\n\n"
        f"<b>{escape_html(state.player1_name)}</b> vs <b>{escape_html(state.player2_name)}</b>\n"
        f"Формат: Best of {state.best_of}\n\n"
        f"Создайте комнату в мультиплеере и выберите карту ниже.\n"
        f"Сейчас выбирает: <b>{escape_html(pick_turn)}</b>\n"
        f"После игры используйте <code>duelresult</code> для проверки скоров.",
        parse_mode="HTML",
    )

    try:
        photo = await card_renderer.generate_duel_pick_card_async({
            "round_number": 1,
            "pick_turn": pick_turn,
            "suggestions": pick_cards,
        })
        await callback.message.answer_photo(photo=BufferedInputFile(photo.read(), filename="duel_pick.png"), reply_markup=keyboard)
    except Exception:
        await callback.message.answer(
            f"Выбери карту на раунд 1 — ходит <b>{escape_html(pick_turn)}</b>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("duel_decline:"))
async def on_duel_decline(callback: CallbackQuery):
    duel_id = int(callback.data.split(":")[1])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        duel = await session.get(Duel, duel_id)
        if not duel or duel.status != "pending":
            await callback.answer("Эта дуэль уже недоступна.", show_alert=True)
            return

        p2 = await session.get(User, duel.player2_user_id)
        if not p2 or p2.telegram_id != tg_id:
            await callback.answer("Этот вызов не для тебя!", show_alert=True)
            return

        duel.status = "cancelled"
        await session.commit()

    await callback.message.edit_text(
        callback.message.text + "\n\n<b>ОТКЛОНЕНО.</b>",
        parse_mode="HTML",
    )
    await callback.answer("Дуэль отклонена.")


@router.callback_query(F.data.startswith("duel_pick:"))
async def on_duel_pick(callback: CallbackQuery):
    if not _duel_manager:
        await callback.answer("Система дуэлей недоступна", show_alert=True)
        return

    parts = callback.data.split(":")
    duel_id = int(parts[1])
    beatmap_id = int(parts[2])

    state = _duel_manager.get_active_state(duel_id)
    if not state:
        await callback.answer("Дуэль не найдена.", show_alert=True)
        return

    async with get_db_session() as session:
        p1 = await session.get(User, state.player1_user_id)
        p2 = await session.get(User, state.player2_user_id)
        allowed_ids = {p1.telegram_id if p1 else None, p2.telegram_id if p2 else None}
    if callback.from_user.id not in allowed_ids:
        await callback.answer("Это не твоя дуэль!", show_alert=True)
        return

    success = _duel_manager.pick_beatmap(duel_id, beatmap_id)
    if not success:
        await callback.answer("Не удалось установить карту.", show_alert=True)
        return

    info = state.mappool_info.get(beatmap_id, {})
    title = info.get("title", "Unknown")
    stars = info.get("star_rating", 0.0)
    map_link = f"https://osu.ppy.sh/b/{beatmap_id}"

    await callback.message.edit_text(
        f"<b>Раунд {state.current_round} — Карта выбрана!</b>\n\n"
        f"<b>{escape_html(title)}</b> ({stars:.2f}\u2605)\n"
        f"<a href=\"{map_link}\">Скачать карту</a>\n\n"
        f"Сейчас выбирает: <b>{escape_html(_duel_manager._pick_turn_name(duel_id))}</b>\n"
        f"Сыграйте эту карту, затем используйте <code>duelresult</code> для проверки.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("duel_custom:"))
async def on_duel_custom(callback: CallbackQuery, state: FSMContext):
    duel_id = int(callback.data.split(":")[1])
    duel_state = _duel_manager.get_active_state(duel_id)
    if not duel_state:
        await callback.answer("Это не твоя дуэль!", show_alert=True)
        return

    async with get_db_session() as session:
        p1 = await session.get(User, duel_state.player1_user_id)
        p2 = await session.get(User, duel_state.player2_user_id)
        allowed_ids = {p1.telegram_id if p1 else None, p2.telegram_id if p2 else None}
    if callback.from_user.id not in allowed_ids:
        await callback.answer("Это не твоя дуэль!", show_alert=True)
        return

    await state.set_state(DuelPickStates.waiting_beatmap_id)
    await state.set_data({"duel_id": duel_id})
    await callback.message.answer(f"Введи ID карты — ходит <b>{escape_html(_duel_manager._pick_turn_name(duel_id))}</b>", parse_mode="HTML")
    await callback.answer()


@router.message(DuelPickStates.waiting_beatmap_id)
async def on_custom_beatmap_id(message: types.Message, state: FSMContext):
    if not _duel_manager:
        await state.clear()
        return

    data = await state.get_data()
    duel_id = data.get("duel_id")
    await state.clear()

    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Неверный ID карты. Должно быть число.")
        return

    beatmap_id = int(text)
    duel_state = _duel_manager.get_active_state(duel_id)
    if not duel_state:
        await message.answer("Дуэль не найдена или уже завершена.")
        return

    async with get_db_session() as session:
        p1 = await session.get(User, duel_state.player1_user_id)
        p2 = await session.get(User, duel_state.player2_user_id)
        allowed_ids = {p1.telegram_id if p1 else None, p2.telegram_id if p2 else None}
    if message.from_user.id not in allowed_ids:
        await message.answer("Это не твоя дуэль!")
        return

    success = _duel_manager.pick_beatmap(duel_id, beatmap_id)
    if success:
        map_link = f"https://osu.ppy.sh/b/{beatmap_id}"
        await message.answer(
            f"<b>Раунд {duel_state.current_round} — Карта выбрана!</b>\n\n"
            f"<a href=\"{map_link}\">Скачать карту</a>\n\n"
            f"Сыграйте эту карту, затем используйте <code>duelresult</code> для проверки.",
            parse_mode="HTML",
        )
    else:
        await message.answer("Не удалось установить карту.")


# Timeout callback (called from DuelManager cleanup loop)

async def _handle_timeout_event(event_type: str, data: dict):
    """Handle timeout events from DuelManager."""
    if not _bot_instance:
        return

    chat_id = data.get("chat_id")
    if not chat_id:
        return

    if event_type == "round_timeout":
        # Round timed out — auto-resolved via force_timeout
        p1_played = data.get("p1_played", False)
        p2_played = data.get("p2_played", False)

        forfeit_msg = ""
        if not p1_played and not p2_played:
            forfeit_msg = "Оба игрока не сыграли. Раунд — ничья."
        elif not p1_played:
            forfeit_msg = f"{escape_html(data['player1_name'])} не сыграл — раунд проигран!"
        elif not p2_played:
            forfeit_msg = f"{escape_html(data['player2_name'])} не сыграл — раунд проигран!"

        try:
            photo = await card_renderer.generate_duel_round_card_async(data)
            await _bot_instance.send_photo(
                chat_id,
                photo=BufferedInputFile(photo.read(), filename="duel_round.png"),
                caption=f"\u23f0 <b>Время вышло!</b> {forfeit_msg}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Failed to send timeout round card: {e}")
            await _bot_instance.send_message(
                chat_id,
                f"\u23f0 <b>Раунд {data.get('round_number', '?')} — Время вышло!</b>\n{forfeit_msg}\n"
                f"Счёт: {data.get('player1_wins', 0)} — {data.get('player2_wins', 0)}",
                parse_mode="HTML",
            )

        # If duel finished after this round, send final card
        if data.get("finished"):
            state = _duel_manager.get_active_state(data["duel_id"]) if _duel_manager else None
            if state:
                rounds = await _duel_manager.get_duel_rounds(state.duel_id)
                winner_name = "DRAW"
                if state.player1_wins > state.player2_wins:
                    winner_name = state.player1_name
                elif state.player2_wins > state.player1_wins:
                    winner_name = state.player2_name
                try:
                    photo = await card_renderer.generate_duel_result_card_async({
                        "player1_name": state.player1_name,
                        "player2_name": state.player2_name,
                        "player1_wins": state.player1_wins,
                        "player2_wins": state.player2_wins,
                        "winner_name": winner_name,
                        "best_of": state.best_of,
                        "rounds": rounds,
                    })
                    await _bot_instance.send_photo(
                        chat_id,
                        photo=BufferedInputFile(photo.read(), filename="duel_result.png"),
                    )
                except Exception:
                    pass
        else:
            # Suggest maps for next round
            if _duel_manager:
                suggestions = _duel_manager.suggest_maps(data["duel_id"])
                keyboard = _make_suggestions_keyboard(data["duel_id"], suggestions)
                await _bot_instance.send_message(
                    chat_id,
                    f"Счёт: <b>{data.get('player1_wins', 0)} — {data.get('player2_wins', 0)}</b>\n"
                    f"Выбери карту на следующий раунд:",
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

    elif event_type == "duel_timeout":
        await _bot_instance.send_message(
            chat_id,
            "<b>Дуэль отменена</b> (лимит 1 час).",
            parse_mode="HTML",
        )


# Helpers

async def _send_final_result(message: types.Message, state):
    """Send the final duel result card."""
    rounds = await _duel_manager.get_duel_rounds(state.duel_id)

    winner_name = "DRAW"
    if state.player1_wins > state.player2_wins:
        winner_name = state.player1_name
    elif state.player2_wins > state.player1_wins:
        winner_name = state.player2_name

    data = {
        "player1_name": state.player1_name,
        "player2_name": state.player2_name,
        "player1_wins": state.player1_wins,
        "player2_wins": state.player2_wins,
        "winner_name": winner_name,
        "best_of": state.best_of,
        "rounds": rounds,
    }

    try:
        photo = await card_renderer.generate_duel_result_card_async(data)
        await message.answer_photo(
            photo=BufferedInputFile(photo.read(), filename="duel_result.png"),
        )
    except Exception as e:
        logger.error(f"Failed to generate result card: {e}")
        await message.answer(
            f"<b>ДУЭЛЬ ЗАВЕРШЕНА!</b>\n"
            f"{escape_html(state.player1_name)} {state.player1_wins} — "
            f"{state.player2_wins} {escape_html(state.player2_name)}\n"
            f"Победитель: <b>{escape_html(winner_name)}</b>",
            parse_mode="HTML",
        )


def _make_suggestions_keyboard(duel_id: int, suggestions: list) -> InlineKeyboardMarkup:
    buttons = []
    for s in suggestions:
        title = s.get("title", "Unknown")
        if len(title) > 30:
            title = title[:27] + "..."
        stars = s.get("star_rating", 0.0)
        label = f"{title} ({stars:.1f}\u2605)"
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"duel_pick:{duel_id}:{s['beatmap_id']}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="Ввести ID карты", callback_data=f"duel_custom:{duel_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _resolve_target(session, osu_api_client, target_name: str):
    """Resolve target by osu! username and report missing status."""
    target, user_data, status = await resolve_osu_query_status(session, osu_api_client, target_name)
    return target, status


__all__ = ["router", "init_duel_handlers"]
