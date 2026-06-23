from datetime import timedelta
from utils.timeutils import utcnow

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.admin.bounty_utils import (
    BOUNTY_TYPES, _generate_bounty_id, _build_summary, _rank_keyboard, _canonical_bounty_type,
)
from db.database import get_db_session
from db.models.bounty import Bounty
from utils.admin_check import AdminFilter
from utils.osu.helpers import extract_beatmap_id
from utils.formatting.text import escape_html, format_error, format_success
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_bounty_create")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


class BountyCreateStates(StatesGroup):
    waiting_beatmap = State()
    waiting_title = State()
    waiting_bounty_type = State()
    waiting_accuracy = State()
    waiting_mods = State()
    waiting_misses = State()
    waiting_rank = State()
    waiting_participants = State()
    waiting_deadline = State()
    waiting_confirm = State()


@router.message(TextTriggerFilter("bountycreate", "bcr"))
async def bountycreate_command(message: types.Message, state: FSMContext, osu_api_client, trigger_args: TriggerArgs = None):
    await state.set_state(BountyCreateStates.waiting_beatmap)
    await message.answer("Отправьте Beatmap ID или ссылку на карту:")


@router.message(BountyCreateStates.waiting_beatmap)
async def create_beatmap(message: types.Message, state: FSMContext, osu_api_client):
    bid = extract_beatmap_id(message.text)
    if not bid:
        await message.answer(format_error("Не удалось распознать beatmap ID. Попробуйте ещё раз:"))
        return

    wait_msg = await message.answer("Загрузка данных карты...")
    beatmap = await osu_api_client.get_beatmap(bid)
    if not beatmap:
        await wait_msg.edit_text(format_error(f"Карта {bid} не найдена."))
        return

    beatmapset = beatmap.get("beatmapset", {})
    artist = beatmapset.get("artist", "Unknown")
    title = beatmapset.get("title", "Unknown")
    version = beatmap.get("version", "Unknown")
    beatmap_title = f"{artist} - {title} [{version}]"
    bset_id = beatmap.get("beatmapset_id") or beatmapset.get("id") or 0

    mapper_id = beatmapset.get("user_id") or beatmap.get("user_id")
    mapper_name = beatmapset.get("creator")
    mapper_avatar_url = None
    if mapper_id:
        try:
            mu = await osu_api_client.get_user_data(int(mapper_id))
            if mu:
                mapper_name = mu.get("username") or mapper_name
                mapper_avatar_url = mu.get("avatar_url")
        except Exception:
            pass

    data = {
        "beatmap_id": int(bid),
        "beatmapset_id": int(bset_id) if bset_id else None,
        "mapper_id": int(mapper_id) if mapper_id else None,
        "mapper_name": mapper_name,
        "mapper_avatar_url": mapper_avatar_url,
        "beatmap_title": beatmap_title,
        "star_rating": float(beatmap.get("difficulty_rating", 0.0)),
        "drain_time": int(beatmap.get("total_length", 0)),
        "cs": float(beatmap.get("cs", 0.0)),
        "od": float(beatmap.get("accuracy", 0.0)),
        "ar": float(beatmap.get("ar", 0.0)),
        "hp_drain": float(beatmap.get("drain", 0.0)),
        "bpm": float(beatmap.get("bpm", 0.0)),
        "max_combo": int(beatmap.get("max_combo", 0)),
    }
    await state.update_data(**data)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить (авто)", callback_data="create_title_skip")]
    ])
    await wait_msg.edit_text(
        f"<b>Карта найдена:</b> {escape_html(beatmap_title)}\n"
        f"<b>Сложность:</b> {data['star_rating']:.2f}★ | "
        f"<b>Длительность:</b> {data['drain_time'] // 60}:{data['drain_time'] % 60:02d}\n\n"
        "Введите название баунти или нажмите «Пропустить»:",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.set_state(BountyCreateStates.waiting_title)


@router.callback_query(F.data == "create_title_skip", BountyCreateStates.waiting_title)
async def create_title_skip(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(title=data["beatmap_title"])
    await callback.answer()
    await _ask_bounty_type(callback.message, state)


@router.message(BountyCreateStates.waiting_title)
async def create_title_text(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await _ask_bounty_type(message, state)


async def _ask_bounty_type(message: types.Message, state: FSMContext):
    rows = []
    for i in range(0, len(BOUNTY_TYPES), 2):
        row = [InlineKeyboardButton(text=t, callback_data=f"create_type_{t}") for t in BOUNTY_TYPES[i:i+2]]
        rows.append(row)
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await state.set_state(BountyCreateStates.waiting_bounty_type)
    await message.answer("Тип баунти?\n(или напишите имя одного из перечисленных типов)", reply_markup=kb)


@router.callback_query(F.data.startswith("create_type_"), BountyCreateStates.waiting_bounty_type)
async def create_type_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.replace("create_type_", "")
    await state.update_data(bounty_type=val)
    await callback.answer()
    await _ask_accuracy(callback.message, state)


@router.message(BountyCreateStates.waiting_bounty_type)
async def create_type_text(message: types.Message, state: FSMContext):
    canonical = _canonical_bounty_type(message.text or "")
    if not canonical:
        await message.answer(
            "Неизвестный тип. Доступные: " + ", ".join(BOUNTY_TYPES) +
            ".\nЛибо выберите кнопкой выше, либо повторите ввод."
        )
        return
    await state.update_data(bounty_type=canonical)
    await _ask_accuracy(message, state)


async def _ask_accuracy(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="95%", callback_data="create_acc_95"),
            InlineKeyboardButton(text="97%", callback_data="create_acc_97"),
        ],
        [
            InlineKeyboardButton(text="98%", callback_data="create_acc_98"),
            InlineKeyboardButton(text="99%", callback_data="create_acc_99"),
        ],
        [InlineKeyboardButton(text="Без ограничения", callback_data="create_acc_none")],
    ])
    await state.set_state(BountyCreateStates.waiting_accuracy)
    await message.answer("Минимальная точность?\n(или введите число, напр. 96.5)", reply_markup=kb)


@router.callback_query(F.data.startswith("create_acc_"), BountyCreateStates.waiting_accuracy)
async def create_accuracy_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[-1]
    acc = float(val) if val != "none" else None
    await state.update_data(min_accuracy=acc)
    await callback.answer()
    await _ask_mods(callback.message, state)


@router.message(BountyCreateStates.waiting_accuracy)
async def create_accuracy_text(message: types.Message, state: FSMContext):
    text = message.text.strip().replace("%", "")
    try:
        acc = float(text)
        if not (0 <= acc <= 100):
            raise ValueError
    except ValueError:
        await message.answer(format_error("Введите число от 0 до 100, или используйте кнопки."))
        return
    await state.update_data(min_accuracy=acc)
    await _ask_mods(message, state)


async def _ask_mods(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="HD", callback_data="create_mods_HD"),
            InlineKeyboardButton(text="HR", callback_data="create_mods_HR"),
        ],
        [
            InlineKeyboardButton(text="HD+HR", callback_data="create_mods_HD,HR"),
            InlineKeyboardButton(text="DT", callback_data="create_mods_DT"),
        ],
        [InlineKeyboardButton(text="Без модов", callback_data="create_mods_none")],
    ])
    await state.set_state(BountyCreateStates.waiting_mods)
    await message.answer("Обязательные моды?\n(или напишите, напр. HD,DT,FL)", reply_markup=kb)


@router.callback_query(F.data.startswith("create_mods_"), BountyCreateStates.waiting_mods)
async def create_mods_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.replace("create_mods_", "")
    mods = val if val != "none" else None
    await state.update_data(required_mods=mods)
    await callback.answer()
    await _ask_misses(callback.message, state)


@router.message(BountyCreateStates.waiting_mods)
async def create_mods_text(message: types.Message, state: FSMContext):
    text = message.text.strip().upper()
    if text in ("0", "-", "NONE", "NO", "НЕТ"):
        await state.update_data(required_mods=None)
    else:
        await state.update_data(required_mods=text)
    await _ask_misses(message, state)


async def _ask_misses(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="0 (FC)", callback_data="create_miss_0"),
            InlineKeyboardButton(text="3", callback_data="create_miss_3"),
        ],
        [
            InlineKeyboardButton(text="5", callback_data="create_miss_5"),
            InlineKeyboardButton(text="10", callback_data="create_miss_10"),
        ],
        [InlineKeyboardButton(text="Без ограничения", callback_data="create_miss_none")],
    ])
    await state.set_state(BountyCreateStates.waiting_misses)
    await message.answer("Максимум миссов?\n(или введите число)", reply_markup=kb)


@router.callback_query(F.data.startswith("create_miss_"), BountyCreateStates.waiting_misses)
async def create_misses_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[-1]
    misses = int(val) if val != "none" else None
    await state.update_data(max_misses=misses)
    await callback.answer()
    await _ask_rank(callback.message, state)


@router.message(BountyCreateStates.waiting_misses)
async def create_misses_text(message: types.Message, state: FSMContext):
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
    await _ask_rank(message, state)


async def _ask_rank(message: types.Message, state: FSMContext):
    kb = _rank_keyboard("create_rank")
    await state.set_state(BountyCreateStates.waiting_rank)
    await message.answer(
        "Минимальный ранг для участия?\n"
        "(или введите кол-во HP, напр. <code>500</code> — только игроки с ≥500 HP)",
        reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("create_rank_"), BountyCreateStates.waiting_rank)
async def create_rank_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.replace("create_rank_", "")
    if val == "none":
        await state.update_data(min_rank=None, min_hp=None)
    else:
        await state.update_data(min_rank=val, min_hp=None)
    await callback.answer()
    await _ask_participants(callback.message, state)


@router.message(BountyCreateStates.waiting_rank)
async def create_rank_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() in ("-", "none", "no", "нет"):
        await state.update_data(min_rank=None, min_hp=None)
    elif text.isdigit() and 0 <= int(text) <= 10000:
        await state.update_data(min_rank=None, min_hp=int(text))
    else:
        await state.update_data(min_rank=text, min_hp=None)
    await _ask_participants(message, state)


async def _ask_participants(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="5", callback_data="create_part_5"),
            InlineKeyboardButton(text="10", callback_data="create_part_10"),
        ],
        [
            InlineKeyboardButton(text="20", callback_data="create_part_20"),
            InlineKeyboardButton(text="Без лимита", callback_data="create_part_none"),
        ],
    ])
    await state.set_state(BountyCreateStates.waiting_participants)
    await message.answer("Макс. участников?\n(или введите число)", reply_markup=kb)


@router.callback_query(F.data.startswith("create_part_"), BountyCreateStates.waiting_participants)
async def create_participants_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[-1]
    part = int(val) if val != "none" else None
    await state.update_data(max_participants=part)
    await callback.answer()
    await _ask_deadline(callback.message, state)


@router.message(BountyCreateStates.waiting_participants)
async def create_participants_text(message: types.Message, state: FSMContext):
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
    await _ask_deadline(message, state)


async def _ask_deadline(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="24ч", callback_data="create_dl_24"),
            InlineKeyboardButton(text="3 дня", callback_data="create_dl_72"),
        ],
        [
            InlineKeyboardButton(text="7 дней", callback_data="create_dl_168"),
            InlineKeyboardButton(text="Без дедлайна", callback_data="create_dl_none"),
        ],
    ])
    await state.set_state(BountyCreateStates.waiting_deadline)
    await message.answer("Дедлайн?\n(или введите часы, напр. 48)", reply_markup=kb)


@router.callback_query(F.data.startswith("create_dl_"), BountyCreateStates.waiting_deadline)
async def create_deadline_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[-1]
    dl = None if val == "none" else utcnow() + timedelta(hours=int(val))
    await state.update_data(deadline=dl)
    await callback.answer()
    await _show_create_confirm(callback.message, state)


@router.message(BountyCreateStates.waiting_deadline)
async def create_deadline_text(message: types.Message, state: FSMContext):
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
        await state.update_data(deadline=utcnow() + timedelta(hours=hours))
    await _show_create_confirm(message, state)


async def _show_create_confirm(message: types.Message, state: FSMContext):
    data = await state.get_data()
    summary = _build_summary(data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Создать", callback_data="create_confirm"),
            InlineKeyboardButton(text="Отмена", callback_data="create_cancel"),
        ]
    ])
    await state.set_state(BountyCreateStates.waiting_confirm)
    await message.answer(summary, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "create_cancel", BountyCreateStates.waiting_confirm)
async def create_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.edit_text("Создание баунти отменено.")


@router.callback_query(F.data == "create_confirm", BountyCreateStates.waiting_confirm)
async def create_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    bounty_id = await _generate_bounty_id()

    async with get_db_session() as session:
        bounty = Bounty(
            bounty_id=bounty_id,
            bounty_type=data.get("bounty_type", "First FC"),
            title=data["title"],
            beatmap_id=data["beatmap_id"],
            beatmapset_id=data.get("beatmapset_id"),
            mapper_id=data.get("mapper_id"),
            mapper_name=data.get("mapper_name"),
            mapper_avatar_url=data.get("mapper_avatar_url"),
            beatmap_title=data["beatmap_title"],
            star_rating=data["star_rating"],
            drain_time=data["drain_time"],
            min_accuracy=data.get("min_accuracy"),
            required_mods=data.get("required_mods"),
            max_misses=data.get("max_misses"),
            min_rank=data.get("min_rank"),
            min_hp=data.get("min_hp"),
            max_participants=data.get("max_participants"),
            cs=data.get("cs", 0.0),
            od=data.get("od", 0.0),
            ar=data.get("ar", 0.0),
            hp_drain=data.get("hp_drain", 0.0),
            bpm=data.get("bpm", 0.0),
            max_combo=data.get("max_combo", 0),
            created_by=callback.from_user.id,
            deadline=data.get("deadline"),
            source="manual",  # explicit so weekly generator's auto-bounties stay distinct
        )
        session.add(bounty)
        await session.commit()

    await state.clear()
    await callback.answer("Баунти создан!")
    await callback.message.edit_text(
        format_success(f"Баунти <b>{escape_html(bounty_id)}</b> создан!\n"
                       f"Название: {escape_html(data['title'])}"),
        parse_mode="HTML"
    )
    logger.info(f"Bounty {bounty_id} created by {callback.from_user.id}")
