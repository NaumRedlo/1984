"""Admin commands for force-closing DUEL duels (selective or bulk)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from aiogram import Router, types, F
from sqlalchemy import select, update as sa_update

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.duel import Duel
from db.models.duel_round import DuelRound
from utils.admin_check import AdminFilter
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_duel_force_close")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

_ACTIVE_STATUSES = ('pending', 'accepted', 'round_active')

# ── Confirmation slot storage ────────────────────────────────────────────────

_close_slots: dict[str, dict] = {}


def _register_slot(tg_id: int, duel_ids: list[int]) -> str:
    slot_id = uuid4().hex[:8]
    _close_slots[slot_id] = {
        "tg_id": tg_id,
        "duel_ids": duel_ids,
        "created_at": datetime.now(timezone.utc),
    }
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    for sid, data in list(_close_slots.items()):
        if data.get("created_at") and data["created_at"] < cutoff:
            _close_slots.pop(sid, None)
    return slot_id


async def _force_cancel_duel(duel_id: int) -> bool:
    """Force-cancel a single duel: DB state + IRC room teardown."""
    now = datetime.now(timezone.utc)

    async with get_db_session() as session:
        duel = (await session.execute(
            select(Duel).where(Duel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status not in _ACTIVE_STATUSES:
            return False

        osu_match_id = duel.osu_match_id

        duel.status = 'cancelled'
        duel.completed_at = now
        duel.pick_candidates = None
        duel.pick_candidates_p1 = None
        duel.pick_candidates_p2 = None
        duel.pick_p1 = None
        duel.pick_p2 = None
        duel.pick_turn = None
        duel.pick_played = None

        await session.execute(
            sa_update(DuelRound)
            .where(
                DuelRound.duel_id == duel_id,
                DuelRound.status.in_(('waiting', 'active')),
            )
            .values(status='cancelled', completed_at=now)
        )
        await session.commit()

    # Clear in-memory state
    from services.duel.duel_state import clear_duel_state
    clear_duel_state(duel_id)

    # Close IRC room
    if osu_match_id:
        try:
            from services.bancho_irc import get_irc_client
            from services.duel.irc_room import close_room
            irc = get_irc_client()
            if irc.connected:
                await close_room(irc, int(osu_match_id))
        except Exception as e:
            logger.warning(f"_force_cancel_duel: IRC close failed for match {osu_match_id}: {e}")

    return True


# ── closeduel <id> — selective force-close ───────────────────────────────────

@router.message(TextTriggerFilter("closeduel"))
async def cmd_close_duel(message: types.Message, trigger_args: TriggerArgs):
    """closeduel <id> — принудительно закрыть конкретную дуэль."""
    args = (trigger_args.args or "").strip()
    if not args or not args.isdigit():
        await message.answer(
            "Использование: <code>closeduel &lt;id&gt;</code>",
            parse_mode="HTML",
        )
        return

    duel_id = int(args)

    async with get_db_session() as session:
        duel = (await session.execute(
            select(Duel).where(Duel.id == duel_id)
        )).scalar_one_or_none()

    if not duel:
        await message.answer(f"Дуэль {duel_id} не найдена.")
        return
    if duel.status not in _ACTIVE_STATUSES:
        await message.answer(
            f"Дуэль {duel_id} уже в статусе <code>{duel.status}</code>.",
            parse_mode="HTML",
        )
        return

    slot = _register_slot(message.from_user.id, [duel_id])
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text=f"Закрыть дуэль {duel_id}",
            callback_data=f"duelclose:apply:{slot}",
        ),
        types.InlineKeyboardButton(
            text="Отмена",
            callback_data=f"duelclose:cancel:{slot}",
        ),
    ]])

    await message.answer(
        f"<b>Принудительное закрытие дуэли</b>\n\n"
        f"ID: <b>{duel_id}</b>\n"
        f"Статус: <code>{duel.status}</code>\n"
        f"Игроки: p1={duel.player1_user_id}, p2={duel.player2_user_id}\n"
        f"IRC match: <code>{duel.osu_match_id or '—'}</code>\n\n"
        "Подтвердить?",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── closeallduels — bulk force-close ─────────────────────────────────────────

@router.message(TextTriggerFilter("closeallduels"))
async def cmd_close_all_duels(message: types.Message, trigger_args: TriggerArgs):
    """closeallduels [active|stuck|all] — закрыть все активные дуэли.

    Фильтры:
      active (default) — pending + accepted + round_active
      stuck            — только round_active (зависшие)
      all              — то же что active
    """
    raw = (trigger_args.args or "").strip().lower()
    if raw == "stuck":
        statuses = ('round_active',)
        label = "round_active (зависшие)"
    else:
        statuses = _ACTIVE_STATUSES
        label = "pending / accepted / round_active"

    async with get_db_session() as session:
        duels = (await session.execute(
            select(Duel).where(Duel.status.in_(statuses))
        )).scalars().all()

    if not duels:
        await message.answer("Нет активных дуэлей для закрытия.")
        return

    duel_ids = [d.id for d in duels]
    lines = []
    for d in duels[:15]:
        lines.append(
            f"  • #{d.id} [{d.status}] p1={d.player1_user_id} p2={d.player2_user_id}"
            f" irc={d.osu_match_id or '—'}"
        )
    if len(duels) > 15:
        lines.append(f"  … и ещё {len(duels) - 15}")

    slot = _register_slot(message.from_user.id, duel_ids)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text=f"Закрыть все ({len(duel_ids)})",
            callback_data=f"duelclose:apply:{slot}",
        ),
        types.InlineKeyboardButton(
            text="Отмена",
            callback_data=f"duelclose:cancel:{slot}",
        ),
    ]])

    await message.answer(
        f"<b>Массовое закрытие дуэлей</b>\n\n"
        f"Фильтр: <b>{label}</b>\n"
        f"Найдено: <b>{len(duel_ids)}</b>\n\n"
        + "\n".join(lines) + "\n\n"
        "Подтвердить?",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── Callback handler (shared for single + bulk) ─────────────────────────────

@router.callback_query(F.data.startswith("duelclose:"))
async def on_duel_close_callback(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    action = parts[1]
    slot_id = parts[2]
    slot = _close_slots.get(slot_id)

    if not slot:
        await callback.answer("Сессия истекла.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не твой запрос.", show_alert=True)
        return

    if action == "cancel":
        _close_slots.pop(slot_id, None)
        try:
            await callback.message.edit_text(
                (callback.message.html_text or "") + "\n\n<b>Отменено.</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass
        await callback.answer("Отменено.")
        return

    if action != "apply":
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    duel_ids = slot["duel_ids"]
    _close_slots.pop(slot_id, None)

    closed = 0
    for duel_id in duel_ids:
        try:
            if await _force_cancel_duel(duel_id):
                closed += 1
        except Exception as e:
            logger.error(f"force_cancel_duel {duel_id} failed: {e}", exc_info=True)

    logger.warning(
        f"duelclose applied by admin tg_id={callback.from_user.id} "
        f"closed={closed}/{len(duel_ids)} ids={duel_ids}"
    )

    try:
        await callback.message.edit_text(
            (callback.message.html_text or "")
            + f"\n\n<b>Закрыто: {closed}/{len(duel_ids)}</b>",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(
            f"Закрыто: <b>{closed}/{len(duel_ids)}</b>",
            parse_mode="HTML",
        )

    await callback.answer(f"Закрыто: {closed}")
