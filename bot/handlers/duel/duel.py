from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.filters import TriggerArgs
from bot.handlers.duel.common import dm, resolve_duel_thread
from db.database import get_db_session
from db.models.duel import Duel
from db.models.duel_rating import DuelRating
from utils.formatting.text import escape_html
from utils.hp_calculator import get_division_for_conservative, DUEL_DIVISION_INDEX
from utils.osu.resolve_user import get_any_user_by_telegram_id, get_registered_user_by_osu
from sqlalchemy import select

router = Router(name="duel.duel")


async def handle_challenge(message: Message, trigger_args: TriggerArgs, osu_api_client, tenant_chat_id=None):
    """duel <nick> [casual|ranked] — challenge a player to a DUEL duel.

    Called by the unified ``duel`` entry-point in profile_panel when args are
    present (bare ``duel`` shows the profile panel instead). ``tenant_chat_id``
    is the effective tenant — the group itself in a group, or the DM-selected
    group — which becomes the new duel's ``chat_id`` (so a duel started in a DM
    is announced in the chosen group, where both players accept it).
    """
    tg_id = message.from_user.id

    raw = (trigger_args.args or "").strip()
    if not raw:
        await message.answer(
            "Использование: <code>duel &lt;osu-ник&gt; [casual|ranked]</code>\n"
            "Пример: <code>duel nazeetskyyy ranked</code>",
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
        challenger = await get_any_user_by_telegram_id(session, tg_id, tenant_chat_id)
        if not challenger or not challenger.osu_user_id:
            await message.answer(
                "Сначала зарегистрируйтесь: <code>register &lt;nickname&gt;</code>",
                parse_mode="HTML",
            )
            return

        opponent = await get_registered_user_by_osu(session, tenant_chat_id, osu_username=osu_nick)

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

    # Division mismatch warning (soft, does not block) — RANKED only. Casual
    # duels are exempt from the TrueSkill/division layer, so they never warn
    # about a skill gap (the casual rating exists only to target the pool SR).
    if mode == "ranked":
        async with get_db_session() as session:
            c_rating = (await session.execute(
                select(DuelRating).where(DuelRating.user_id == challenger.id, DuelRating.mode == mode)
            )).scalar_one_or_none()
            o_rating = (await session.execute(
                select(DuelRating).where(DuelRating.user_id == opponent.id, DuelRating.mode == mode)
            )).scalar_one_or_none()

        # Skip the warning while either player is still calibrating: in placement
        # σ is high, so conservative (μ − 2σ) is uncertainty-deflated and the
        # division it maps to is noise — a gap between two calibrating players is
        # an artefact, not a real skill mismatch. Mirrors the finish-card gating
        # (round_engine `was_calibrating`), which also stays quiet in placement.
        c_calibrating = (c_rating.placement_matches_left or 0) > 0 if c_rating else True
        o_calibrating = (o_rating.placement_matches_left or 0) > 0 if o_rating else True

        if c_rating and o_rating and not (c_calibrating or o_calibrating):
            c_div = get_division_for_conservative(c_rating.conservative)
            o_div = get_division_for_conservative(o_rating.conservative)
            div_diff = abs(DUEL_DIVISION_INDEX[c_div] - DUEL_DIVISION_INDEX[o_div])
            if div_diff > 2:
                await message.answer(
                    f"⚠️ Большая разница в дивизионах: <b>{c_div}</b> vs <b>{o_div}</b>.\n"
                    "Дуэль всё равно будет создана.",
                    parse_mode="HTML",
                )

    duel = await dm.create_duel(
        bot=message.bot,
        chat_id=tenant_chat_id,
        challenger_id=challenger.id,
        opponent_id=opponent.id,
        mode=mode,
        osu_api=osu_api_client,
        thread_id=resolve_duel_thread(message),
    )
    if not duel:
        await message.answer(
            "Не удалось создать дуэль — один из игроков уже в активной дуэли.",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("dueld:accept:"))
async def on_dueld_accept(callback: CallbackQuery, osu_api_client, tenant_chat_id=None):
    duel_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id, tenant_chat_id)
        if not user:
            await callback.answer("Сначала зарегистрируйтесь.", show_alert=True)
            return

        duel = (await session.execute(
            select(Duel).where(Duel.id == duel_id)
        )).scalar_one_or_none()

        if not duel:
            await callback.answer("Дуэль не найдена.", show_alert=True)
            return
        if duel.player2_user_id != user.id:
            await callback.answer("Этот вызов не для вас!", show_alert=True)
            return

    await callback.answer("Принимаю дуэль...")
    ok = await dm.accept_duel(callback.bot, duel_id, user.id, osu_api_client,
                              event_chat_id=tenant_chat_id)
    if not ok:
        await callback.answer("Не удалось принять дуэль (истекла или уже принята).", show_alert=True)


@router.callback_query(F.data.startswith("dueld:decline:"))
async def on_dueld_decline(callback: CallbackQuery, tenant_chat_id=None):
    duel_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id, tenant_chat_id)
        if not user:
            await callback.answer("Сначала зарегистрируйтесь.", show_alert=True)
            return

    await callback.answer("Отклоняю...")
    ok = await dm.decline_duel(callback.bot, duel_id, user.id,
                               event_chat_id=tenant_chat_id)
    if not ok:
        await callback.answer("Не удалось отклонить дуэль.", show_alert=True)


@router.callback_query(F.data.startswith("dueld:pick:"))
async def on_dueld_pick(callback: CallbackQuery):
    """Map pick from a player's own pool: dueld:pick:<duel_id>:<beatmap_id>."""
    from services.duel import pick_phase
    parts = callback.data.split(":")
    try:
        duel_id, beatmap_id = int(parts[2]), int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Некорректный выбор.", show_alert=True)
        return

    result = pick_phase.submit_pick(duel_id, callback.from_user.id, beatmap_id)
    if result == "ok":
        await callback.answer("Карта выбрана!")
    elif result == "not_your_turn":
        await callback.answer("Сейчас не ваш ход.", show_alert=True)
    elif result == "invalid":
        await callback.answer("Эта карта уже недоступна.", show_alert=True)
    else:  # not_pending
        await callback.answer("Время выбора истекло.", show_alert=True)


@router.callback_query(F.data.startswith("dueld:swap:"))
async def on_dueld_swap(callback: CallbackQuery):
    """Pre-round-1 pool swap: dueld:swap:<duel_id>:<beatmap_id> → reroll that
    card from the player's personal pool."""
    from services.duel import pool_swap
    parts = callback.data.split(":")
    try:
        duel_id, beatmap_id = int(parts[2]), int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Некорректный выбор.", show_alert=True)
        return

    result = await pool_swap.submit_swap(duel_id, callback.from_user.id, beatmap_id)
    if result == "ok":
        await callback.answer("Заменено.")
    elif result == "not_your_turn":
        await callback.answer("Это не ваш пул.", show_alert=True)
    elif result == "invalid":
        await callback.answer("Эта карта уже не в пуле.", show_alert=True)
    elif result == "out_of_swaps":
        await callback.answer("Лимит замен исчерпан.", show_alert=True)
    elif result == "no_candidate":
        await callback.answer("Нет свежих карт под уровень — попробуйте другую.", show_alert=True)
    else:  # not_pending
        await callback.answer("Окно подгонки уже закрылось.", show_alert=True)


@router.callback_query(F.data.startswith("dueld:swapdone:"))
async def on_dueld_swap_done(callback: CallbackQuery):
    """Pre-round-1 swap: ✅ Готово."""
    from services.duel import pool_swap
    parts = callback.data.split(":")
    try:
        duel_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    result = pool_swap.submit_done(duel_id, callback.from_user.id)
    if result == "ok":
        await callback.answer("Пул зафиксирован.")
    elif result == "not_your_turn":
        await callback.answer("Это не ваш пул.", show_alert=True)
    else:
        await callback.answer()
