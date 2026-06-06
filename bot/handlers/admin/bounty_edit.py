from datetime import datetime, timedelta

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.admin.bounty_utils import (
    BOUNTY_TYPES, EDIT_COOLDOWN_HOURS, _build_summary, _canonical_bounty_type,
    _rank_keyboard,
)
from db.database import get_db_session
from db.models.bounty import Bounty
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html, format_error, format_success
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_bounty_edit")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


class BountyEditStates(StatesGroup):
    waiting_bounty_type = State()
    waiting_accuracy = State()
    waiting_mods = State()
    waiting_misses = State()
    waiting_rank = State()
    waiting_participants = State()
    waiting_deadline = State()
    waiting_confirm = State()


@router.message(TextTriggerFilter("bountyedit", "bed"))
async def bountyedit_command(message: types.Message, trigger_args: TriggerArgs, state: FSMContext):
    bounty_id = trigger_args.args
    if not bounty_id:
        await message.answer(format_error("Использование: bountyedit <bounty_id>"))
        return

    async with get_db_session() as session:
        stmt = select(Bounty).where(Bounty.bounty_id == bounty_id.strip())
        bounty = (await session.execute(stmt)).scalar_one_or_none()
        if not bounty:
            await message.answer(format_error(f"Баунти {escape_html(bounty_id)} не найден."), parse_mode="HTML")
            return

        # Editing a closed/expired/approved bounty silently no-ops with side
        # effects (cooldown stamp, lost edits) — reject up front.
        if bounty.status != "active":
            await message.answer(
                format_error(f"Баунти не активен (статус: {bounty.status}). Редактировать можно только активные.")
            )
            return

        # Auto/weekly bounties are owned by the generator — manual edits would
        # be clobbered on the next regen. Disallow.
        if bounty.source == "auto":
            await message.answer(
                format_error("Это auto-баунти из недельного пула — его нельзя редактировать вручную.")
            )
            return

        if bounty.last_edited_at:
            elapsed = datetime.utcnow() - bounty.last_edited_at
            if elapsed < timedelta(hours=EDIT_COOLDOWN_HOURS):
                remaining = timedelta(hours=EDIT_COOLDOWN_HOURS) - elapsed
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes = remainder // 60
                await message.answer(
                    format_error(f"Кулдаун редактирования. Повторите через {hours}ч {minutes}мин.")
                )
                return

        await state.update_data(
            edit_bounty_id=bounty.bounty_id,
            beatmap_id=bounty.beatmap_id,
            beatmap_title=bounty.beatmap_title,
            title=bounty.title,
            star_rating=bounty.star_rating,
            drain_time=bounty.drain_time,
            cs=bounty.cs, od=bounty.od, ar=bounty.ar,
            hp_drain=bounty.hp_drain, bpm=bounty.bpm, max_combo=bounty.max_combo,
            bounty_type=bounty.bounty_type or "First FC",
            min_accuracy=bounty.min_accuracy,
            required_mods=bounty.required_mods,
            max_misses=bounty.max_misses,
            min_rank=bounty.min_rank,
            min_hp=bounty.min_hp,
            max_participants=bounty.max_participants,
            deadline=bounty.deadline,
        )

    await _ask_edit_bounty_type(message, state)


async def _ask_edit_bounty_type(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current = data.get('bounty_type', 'First FC')
    rows = []
    for i in range(0, len(BOUNTY_TYPES), 2):
        row = [InlineKeyboardButton(text=t, callback_data=f"edit_type_{t}") for t in BOUNTY_TYPES[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton(text=f"Оставить ({current})", callback_data="edit_type_keep")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await state.set_state(BountyEditStates.waiting_bounty_type)
    await message.answer(f"Тип баунти? (текущий: {current})\n(или напишите свой)", reply_markup=kb)


@router.callback_query(F.data.startswith("edit_type_"), BountyEditStates.waiting_bounty_type)
async def edit_type_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.replace("edit_type_", "")
    if val != "keep":
        await state.update_data(bounty_type=val)
    await callback.answer()
    await _ask_edit_accuracy(callback.message, state)


@router.message(BountyEditStates.waiting_bounty_type)
async def edit_type_text(message: types.Message, state: FSMContext):
    canonical = _canonical_bounty_type(message.text or "")
    if not canonical:
        await message.answer(
            "Неизвестный тип. Доступные: " + ", ".join(BOUNTY_TYPES) +
            ".\nЛибо выберите кнопкой выше, либо повторите ввод."
        )
        return
    await state.update_data(bounty_type=canonical)
    await _ask_edit_accuracy(message, state)


async def _ask_edit_accuracy(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current = f"{data['min_accuracy']}%" if data.get('min_accuracy') is not None else "Без ограничения"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="95%", callback_data="edit_acc_95"),
            InlineKeyboardButton(text="97%", callback_data="edit_acc_97"),
        ],
        [
            InlineKeyboardButton(text="98%", callback_data="edit_acc_98"),
            InlineKeyboardButton(text="99%", callback_data="edit_acc_99"),
        ],
        [
            InlineKeyboardButton(text="Без ограничения", callback_data="edit_acc_none"),
            InlineKeyboardButton(text=f"Оставить ({current})", callback_data="edit_acc_keep"),
        ],
    ])
    await state.set_state(BountyEditStates.waiting_accuracy)
    await message.answer(f"Мин. точность? (текущая: {current})\n(или введите число)", reply_markup=kb)


@router.callback_query(F.data.startswith("edit_acc_"), BountyEditStates.waiting_accuracy)
async def edit_accuracy_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[-1]
    if val != "keep":
        acc = float(val) if val != "none" else None
        await state.update_data(min_accuracy=acc)
    await callback.answer()
    await _ask_edit_mods(callback.message, state)


@router.message(BountyEditStates.waiting_accuracy)
async def edit_accuracy_text(message: types.Message, state: FSMContext):
    text = message.text.strip().replace("%", "")
    try:
        acc = float(text)
        if not (0 <= acc <= 100):
            raise ValueError
    except ValueError:
        await message.answer(format_error("Введите число от 0 до 100, или используйте кнопки."))
        return
    await state.update_data(min_accuracy=acc)
    await _ask_edit_mods(message, state)


async def _ask_edit_mods(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current = data.get('required_mods') or "Нет"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="HD", callback_data="edit_mods_HD"),
            InlineKeyboardButton(text="HR", callback_data="edit_mods_HR"),
        ],
        [
            InlineKeyboardButton(text="HD+HR", callback_data="edit_mods_HD,HR"),
            InlineKeyboardButton(text="DT", callback_data="edit_mods_DT"),
        ],
        [
            InlineKeyboardButton(text="Без модов", callback_data="edit_mods_none"),
            InlineKeyboardButton(text=f"Оставить ({current})", callback_data="edit_mods_keep"),
        ],
    ])
    await state.set_state(BountyEditStates.waiting_mods)
    await message.answer(f"Обязательные моды? (текущие: {current})\n(или напишите, напр. HD,DT,FL)", reply_markup=kb)


@router.callback_query(F.data.startswith("edit_mods_"), BountyEditStates.waiting_mods)
async def edit_mods_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.replace("edit_mods_", "")
    if val != "keep":
        mods = val if val != "none" else None
        await state.update_data(required_mods=mods)
    await callback.answer()
    await _ask_edit_misses(callback.message, state)


@router.message(BountyEditStates.waiting_mods)
async def edit_mods_text(message: types.Message, state: FSMContext):
    text = message.text.strip().upper()
    if text in ("0", "-", "NONE", "NO", "НЕТ"):
        await state.update_data(required_mods=None)
    else:
        await state.update_data(required_mods=text)
    await _ask_edit_misses(message, state)


async def _ask_edit_misses(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current = str(data['max_misses']) if data.get('max_misses') is not None else "Без ограничения"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="0 (FC)", callback_data="edit_miss_0"),
            InlineKeyboardButton(text="3", callback_data="edit_miss_3"),
        ],
        [
            InlineKeyboardButton(text="5", callback_data="edit_miss_5"),
            InlineKeyboardButton(text="10", callback_data="edit_miss_10"),
        ],
        [
            InlineKeyboardButton(text="Без ограничения", callback_data="edit_miss_none"),
            InlineKeyboardButton(text=f"Оставить ({current})", callback_data="edit_miss_keep"),
        ],
    ])
    await state.set_state(BountyEditStates.waiting_misses)
    await message.answer(f"Макс. миссов? (текущее: {current})\n(или введите число)", reply_markup=kb)


@router.callback_query(F.data.startswith("edit_miss_"), BountyEditStates.waiting_misses)
async def edit_misses_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[-1]
    if val != "keep":
        misses = int(val) if val != "none" else None
        await state.update_data(max_misses=misses)
    await callback.answer()
    await _ask_edit_rank(callback.message, state)


@router.message(BountyEditStates.waiting_misses)
async def edit_misses_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() in ("-", "none", "no", "нет"):
        await state.update_data(max_misses=None)
    else:
        try:
            val = int(text)
            if not (0 <= val <= 10000):
                raise ValueError
        except ValueError:
            await message.answer(format_error("Введите число от 0 до 10000, или используйте кнопки."))
            return
        await state.update_data(max_misses=val)
    await _ask_edit_rank(message, state)


async def _ask_edit_rank(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if data.get('min_hp') is not None:
        current = f"{data['min_hp']} HP"
    elif data.get('min_rank'):
        current = data['min_rank']
    else:
        current = "Нет"
    kb = _rank_keyboard("edit_rank", include_keep=True, current=current)
    await state.set_state(BountyEditStates.waiting_rank)
    await message.answer(
        f"Мин. ранг для участия? (текущий: {current})\n"
        f"(или введите кол-во HP, напр. <code>500</code>)",
        reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("edit_rank_"), BountyEditStates.waiting_rank)
async def edit_rank_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.replace("edit_rank_", "")
    if val == "keep":
        pass
    elif val == "none":
        await state.update_data(min_rank=None, min_hp=None)
    else:
        await state.update_data(min_rank=val, min_hp=None)
    await callback.answer()
    await _ask_edit_participants(callback.message, state)


@router.message(BountyEditStates.waiting_rank)
async def edit_rank_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() in ("-", "none", "no", "нет"):
        await state.update_data(min_rank=None, min_hp=None)
    elif text.isdigit() and 0 <= int(text) <= 10000:
        await state.update_data(min_rank=None, min_hp=int(text))
    else:
        await state.update_data(min_rank=text, min_hp=None)
    await _ask_edit_participants(message, state)


async def _ask_edit_participants(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current = str(data['max_participants']) if data.get('max_participants') is not None else "Без лимита"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="5", callback_data="edit_part_5"),
            InlineKeyboardButton(text="10", callback_data="edit_part_10"),
        ],
        [
            InlineKeyboardButton(text="20", callback_data="edit_part_20"),
            InlineKeyboardButton(text="Без лимита", callback_data="edit_part_none"),
        ],
        [InlineKeyboardButton(text=f"Оставить ({current})", callback_data="edit_part_keep")],
    ])
    await state.set_state(BountyEditStates.waiting_participants)
    await message.answer(f"Макс. участников? (текущее: {current})\n(или введите число)", reply_markup=kb)


@router.callback_query(F.data.startswith("edit_part_"), BountyEditStates.waiting_participants)
async def edit_participants_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[-1]
    if val != "keep":
        part = int(val) if val != "none" else None
        await state.update_data(max_participants=part)
    await callback.answer()
    await _ask_edit_deadline(callback.message, state)


@router.message(BountyEditStates.waiting_participants)
async def edit_participants_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() in ("-", "none", "no", "нет"):
        await state.update_data(max_participants=None)
    else:
        try:
            val = int(text)
            if not (1 <= val <= 1000):
                raise ValueError
        except ValueError:
            await message.answer(format_error("Введите число от 1 до 1000, или используйте кнопки."))
            return
        await state.update_data(max_participants=val)
    await _ask_edit_deadline(message, state)


async def _ask_edit_deadline(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current = data['deadline'].strftime('%d.%m.%Y %H:%M') if data.get('deadline') else "Нет"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="24ч", callback_data="edit_dl_24"),
            InlineKeyboardButton(text="3 дня", callback_data="edit_dl_72"),
        ],
        [
            InlineKeyboardButton(text="7 дней", callback_data="edit_dl_168"),
            InlineKeyboardButton(text="Без дедлайна", callback_data="edit_dl_none"),
        ],
        [InlineKeyboardButton(text=f"Оставить ({current})", callback_data="edit_dl_keep")],
    ])
    await state.set_state(BountyEditStates.waiting_deadline)
    await message.answer(f"Дедлайн? (текущий: {current})\n(или введите часы, напр. 48)", reply_markup=kb)


@router.callback_query(F.data.startswith("edit_dl_"), BountyEditStates.waiting_deadline)
async def edit_deadline_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[-1]
    if val != "keep":
        dl = None if val == "none" else datetime.utcnow() + timedelta(hours=int(val))
        await state.update_data(deadline=dl)
    await callback.answer()
    await _show_edit_confirm(callback.message, state)


@router.message(BountyEditStates.waiting_deadline)
async def edit_deadline_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() in ("-", "none", "no", "нет", "0"):
        await state.update_data(deadline=None)
    else:
        try:
            hours = int(text)
            if not (1 <= hours <= 8760):
                raise ValueError
        except ValueError:
            await message.answer(format_error("Введите кол-во часов (1–8760), или используйте кнопки."))
            return
        await state.update_data(deadline=datetime.utcnow() + timedelta(hours=hours))
    await _show_edit_confirm(message, state)


async def _show_edit_confirm(message: types.Message, state: FSMContext):
    data = await state.get_data()
    summary = _build_summary(data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сохранить", callback_data="edit_confirm"),
            InlineKeyboardButton(text="Отмена", callback_data="edit_cancel"),
        ]
    ])
    await state.set_state(BountyEditStates.waiting_confirm)
    await message.answer(summary, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "edit_cancel", BountyEditStates.waiting_confirm)
async def edit_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.edit_text("Редактирование баунти отменено.")


@router.callback_query(F.data == "edit_confirm", BountyEditStates.waiting_confirm)
async def edit_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    bid = data["edit_bounty_id"]

    async with get_db_session() as session:
        stmt = select(Bounty).where(Bounty.bounty_id == bid)
        bounty = (await session.execute(stmt)).scalar_one_or_none()
        if not bounty:
            await callback.message.edit_text(format_error("Баунти не найден."))
            await state.clear()
            return

        bounty.bounty_type = data.get("bounty_type", "First FC")
        bounty.min_accuracy = data.get("min_accuracy")
        bounty.required_mods = data.get("required_mods")
        bounty.max_misses = data.get("max_misses")
        bounty.min_rank = data.get("min_rank")
        bounty.min_hp = data.get("min_hp")
        bounty.max_participants = data.get("max_participants")
        bounty.deadline = data.get("deadline")
        bounty.last_edited_at = datetime.utcnow()
        await session.commit()

    await state.clear()
    await callback.answer("Сохранено!")
    await callback.message.edit_text(
        format_success(f"Баунти <b>{escape_html(bid)}</b> обновлён."),
        parse_mode="HTML"
    )
    logger.info(f"Bounty {bid} edited by {callback.from_user.id}")
