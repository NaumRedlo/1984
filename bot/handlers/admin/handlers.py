from datetime import datetime, timedelta
from uuid import uuid4
import io

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func

from bot.filters import TextTriggerFilter, TriggerArgs

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from utils.admin_check import AdminFilter
from utils.osu.helpers import extract_beatmap_id, get_community_stats
from utils.osu.resolve_user import get_any_user_by_telegram_id
from utils.hp_calculator import calculate_hps, get_rank_for_hp
from utils.logger import get_logger
from utils.formatting.text import escape_html, format_error, format_success

logger = get_logger(__name__)

router = Router(name="admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

EDIT_COOLDOWN_HOURS = 4

BOUNTY_TYPES = [
    "First FC", "Snipe", "History", "Accuracy",
    "Pass", "Mod", "SS", "Marathon",
    "Memory", "Metronome", "Easter Egg",
]


# FSM States

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


class BountyEditStates(StatesGroup):
    waiting_bounty_type = State()
    waiting_accuracy = State()
    waiting_mods = State()
    waiting_misses = State()
    waiting_rank = State()
    waiting_participants = State()
    waiting_deadline = State()
    waiting_confirm = State()


# Helpers

async def _generate_bounty_id() -> str:
    today = datetime.utcnow().strftime("%Y.%m.%d")
    return f"{today}/{uuid4().hex[:8]}"


def _build_summary(data: dict) -> str:
    lines = [
        "<b>Сводка баунти</b>",
        "═" * 28,
        f"<b>Тип:</b> {escape_html(data.get('bounty_type', 'First FC'))}",
        f"<b>Карта:</b> {escape_html(data['beatmap_title'])}",
        f"<b>Beatmap ID:</b> {data['beatmap_id']}",
        f"<b>Сложность:</b> {data['star_rating']:.2f}★",
        f"<b>Длительность:</b> {data['drain_time'] // 60}:{data['drain_time'] % 60:02d}",
        f"<b>Название:</b> {escape_html(data['title'])}",
        "═" * 28,
    ]
    if data.get('min_accuracy') is not None:
        lines.append(f"<b>Мин. точность:</b> {data['min_accuracy']}%")
    else:
        lines.append("<b>Мин. точность:</b> Без ограничения")
    lines.append(f"<b>Обязательные моды:</b> {data.get('required_mods') or 'Нет'}")
    if data.get('max_misses') is not None:
        lines.append(f"<b>Макс. миссов:</b> {data['max_misses']}")
    else:
        lines.append("<b>Макс. миссов:</b> Без ограничения")

    # Rank / HP requirement
    rank_text = data.get('min_rank') or "Нет"
    hp_text = f"{data['min_hp']} HP" if data.get('min_hp') is not None else None
    if hp_text and rank_text != "Нет":
        lines.append(f"<b>Мин. ранг:</b> {rank_text} (или {hp_text})")
    elif hp_text:
        lines.append(f"<b>Мин. HP:</b> {hp_text}")
    else:
        lines.append(f"<b>Мин. ранг:</b> {rank_text}")

    if data.get('max_participants') is not None:
        lines.append(f"<b>Макс. участников:</b> {data['max_participants']}")
    else:
        lines.append("<b>Макс. участников:</b> Без лимита")
    if data.get('deadline'):
        lines.append(f"<b>Дедлайн:</b> {data['deadline'].strftime('%d.%m.%Y %H:%M UTC')}")
    else:
        lines.append("<b>Дедлайн:</b> Нет")
    return "\n".join(lines)


def _rank_keyboard(prefix: str, include_keep: bool = False, current: str = ""):
    rows = [
        [
            InlineKeyboardButton(text="Candidate", callback_data=f"{prefix}_Candidate"),
            InlineKeyboardButton(text="Party Member", callback_data=f"{prefix}_Party Member"),
        ],
        [
            InlineKeyboardButton(text="Inspector", callback_data=f"{prefix}_Inspector"),
            InlineKeyboardButton(text="High Commissioner", callback_data=f"{prefix}_High Commissioner"),
        ],
        [
            InlineKeyboardButton(text="Big Brother", callback_data=f"{prefix}_Big Brother"),
            InlineKeyboardButton(text="Без ограничения", callback_data=f"{prefix}_none"),
        ],
    ]
    if include_keep:
        rows.append([InlineKeyboardButton(text=f"Оставить ({current})", callback_data=f"{prefix}_keep")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# /bountycreate (/bcr)

@router.message(TextTriggerFilter("bountycreate", "bcr"))
async def bountycreate_command(message: types.Message, state: FSMContext, osu_api_client, trigger_args: TriggerArgs = None):
    await state.set_state(BountyCreateStates.waiting_beatmap)
    await message.answer("Отправьте Beatmap ID или ссылку на карту:")


# Шаг 1: Карта

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

    data = {
        "beatmap_id": int(bid),
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


# Шаг 2: Название

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


# Шаг 3: Тип баунти

async def _ask_bounty_type(message: types.Message, state: FSMContext):
    rows = []
    for i in range(0, len(BOUNTY_TYPES), 2):
        row = [InlineKeyboardButton(text=t, callback_data=f"create_type_{t}") for t in BOUNTY_TYPES[i:i+2]]
        rows.append(row)
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await state.set_state(BountyCreateStates.waiting_bounty_type)
    await message.answer("Тип баунти?\n(или напишите свой)", reply_markup=kb)


@router.callback_query(F.data.startswith("create_type_"), BountyCreateStates.waiting_bounty_type)
async def create_type_cb(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.replace("create_type_", "")
    await state.update_data(bounty_type=val)
    await callback.answer()
    await _ask_accuracy(callback.message, state)


@router.message(BountyCreateStates.waiting_bounty_type)
async def create_type_text(message: types.Message, state: FSMContext):
    await state.update_data(bounty_type=message.text.strip())
    await _ask_accuracy(message, state)


# Шаг 4: Точность

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


# Шаг 5: Моды

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


# Шаг 6: Миссы

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


# Шаг 7: Мин. ранг / HP

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


# Шаг 8: Участники

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


# Шаг 9: Дедлайн

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
    dl = None if val == "none" else datetime.utcnow() + timedelta(hours=int(val))
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
        await state.update_data(deadline=datetime.utcnow() + timedelta(hours=hours))
    await _show_create_confirm(message, state)


# Шаг 10: Подтверждение

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


# /bountyclose (/bcl)

@router.message(TextTriggerFilter("bountyclose", "bcl"))
async def bountyclose_command(message: types.Message, trigger_args: TriggerArgs):
    bounty_id = trigger_args.args
    if not bounty_id:
        await message.answer(format_error("Использование: bountyclose <bounty_id>"))
        return

    async with get_db_session() as session:
        stmt = select(Bounty).where(Bounty.bounty_id == bounty_id.strip())
        bounty = (await session.execute(stmt)).scalar_one_or_none()
        if not bounty:
            await message.answer(format_error(f"Баунти {escape_html(bounty_id)} не найден."), parse_mode="HTML")
            return
        if bounty.status == "closed":
            await message.answer(format_error("Баунти уже закрыт."))
            return

        bounty.status = "closed"
        bounty.closed_at = datetime.utcnow()
        await session.commit()

    await message.answer(format_success(f"Баунти <b>{escape_html(bounty_id)}</b> закрыт."), parse_mode="HTML")
    logger.info(f"Bounty {bounty_id} closed by {message.from_user.id}")


# /bountydelete (/bdl)

@router.message(TextTriggerFilter("bountydelete", "bdl"))
async def bountydelete_command(message: types.Message, trigger_args: TriggerArgs):
    bounty_id = trigger_args.args
    if not bounty_id:
        await message.answer(format_error("Использование: bountydelete <bounty_id>"))
        return

    async with get_db_session() as session:
        stmt = select(Bounty).where(Bounty.bounty_id == bounty_id.strip())
        bounty = (await session.execute(stmt)).scalar_one_or_none()
        if not bounty:
            await message.answer(format_error(f"Баунти {escape_html(bounty_id)} не найден."), parse_mode="HTML")
            return

        sub_stmt = select(Submission).where(Submission.bounty_id == bounty_id.strip())
        subs = (await session.execute(sub_stmt)).scalars().all()
        for s in subs:
            await session.delete(s)
        await session.delete(bounty)
        await session.commit()

    await message.answer(format_success(f"Баунти <b>{escape_html(bounty_id)}</b> удалён."), parse_mode="HTML")
    logger.info(f"Bounty {bounty_id} deleted by {message.from_user.id}")


# /bountyedit (/bed)

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


# Ред.: Тип баунти

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
    await state.update_data(bounty_type=message.text.strip())
    await _ask_edit_accuracy(message, state)


# Ред.: Точность

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


# Ред.: Моды

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


# Ред.: Миссы

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


# Ред.: Ранг / HP

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


# Ред.: Участники

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


# Ред.: Дедлайн

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


# Ред.: Подтверждение

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


# ─── BSK Map Pool Admin Commands ─────────────────────────────────────────────

@router.message(TextTriggerFilter("bskaddmap"))
async def cmd_bsk_add_map(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """bskaddmap <beatmap_id> — fetch, parse .osu and add to BSK pool."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer(
            "Использование: <code>bskaddmap &lt;beatmap_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    beatmap_id = int(raw)
    wait = await message.answer(f"Загружаю карту {beatmap_id}...")

    try:
        from services.bsk.map_pool import add_map_to_pool
        from services.bsk.osu_parser import extract_features, weights_from_features, map_type_from_weights
        from db.models.bsk_map_pool import BskMapPool
        import aiohttp

        # Check if already in pool
        async with get_db_session() as session:
            existing = (await session.execute(
                select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
            )).scalar_one_or_none()

        if existing:
            await wait.edit_text(
                f"Карта <b>{beatmap_id}</b> уже в пуле: "
                f"{existing.artist} - {existing.title} [{existing.version}] "
                f"({existing.star_rating:.2f}★, type={existing.map_type})",
                parse_mode="HTML",
            )
            return

        # Fetch beatmap metadata
        bmap_data = await osu_api_client.get_beatmap(beatmap_id)
        if not bmap_data:
            await wait.edit_text(f"Карта {beatmap_id} не найдена в osu! API.")
            return

        bset = bmap_data.get("beatmapset") or {}
        bpm = float(bmap_data.get("bpm") or bset.get("bpm") or 0)
        ar = float(bmap_data.get("ar") or 0)
        od = float(bmap_data.get("accuracy") or 0)
        cs = float(bmap_data.get("cs") or 0)
        sr = float(bmap_data.get("difficulty_rating") or 0)
        length = int(bmap_data.get("total_length") or 0)
        beatmapset_id = int(bmap_data.get("beatmapset_id") or bset.get("id") or 0)

        # Download .osu file for parsing
        osu_text = None
        osu_url = f"https://osu.ppy.sh/osu/{beatmap_id}"
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(osu_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        raw_bytes = await resp.read()
                        osu_text = raw_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to download .osu for {beatmap_id}: {e}")

        if osu_text:
            features = extract_features(osu_text)
            weights = weights_from_features(features, bpm=bpm, ar=ar, od=od)
            map_type = map_type_from_weights(weights)
            source = "parsed"
        else:
            # Fallback to heuristic
            from services.bsk.map_pool import _estimate_weights, _map_type
            weights = _estimate_weights(bpm, ar, od, length)
            map_type = _map_type(weights)
            features = {}
            source = "heuristic"

        async with get_db_session() as session:
            entry = BskMapPool(
                beatmap_id=beatmap_id,
                beatmapset_id=beatmapset_id,
                title=bset.get("title") or "Unknown",
                artist=bset.get("artist") or "Unknown",
                version=bmap_data.get("version") or "",
                creator=bset.get("creator"),
                star_rating=sr,
                bpm=bpm,
                length=length,
                ar=ar,
                od=od,
                cs=cs,
                w_aim=weights["aim"],
                w_speed=weights["speed"],
                w_acc=weights["acc"],
                w_cons=weights["cons"],
                map_type=map_type,
                enabled=True,
            )
            session.add(entry)
            await session.commit()

        feat_line = ""
        if features:
            feat_line = (
                f"\nstream: <code>{features.get('stream_density', 0):.3f}</code>  "
                f"jump: <code>{features.get('jump_density', 0):.3f}</code>  "
                f"slider: <code>{features.get('slider_density', 0):.3f}</code>  "
                f"rhythm: <code>{features.get('rhythm_complexity', 0):.3f}</code>"
            )

        await wait.edit_text(
            f"✅ <b>Карта добавлена в BSK пул</b> ({source})\n\n"
            f"<b>{escape_html(bset.get('artist', ''))} - {escape_html(bset.get('title', ''))}</b> "
            f"[{escape_html(bmap_data.get('version', ''))}]\n"
            f"⭐ {sr:.2f}  ·  {bpm:.0f} BPM  ·  AR {ar}  ·  OD {od}\n\n"
            f"Тип: <b>{map_type}</b>\n"
            f"Aim: <code>{weights['aim']:.3f}</code>  "
            f"Speed: <code>{weights['speed']:.3f}</code>  "
            f"Acc: <code>{weights['acc']:.3f}</code>  "
            f"Cons: <code>{weights['cons']:.3f}</code>"
            f"{feat_line}",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"bskaddmap error for {beatmap_id}: {e}", exc_info=True)
        await wait.edit_text(f"Ошибка: {e}")


@router.message(TextTriggerFilter("bskremovemap"))
async def cmd_bsk_remove_map(message: types.Message, trigger_args: TriggerArgs):
    """bskremovemap <beatmap_id> — disable map in BSK pool."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer("Использование: <code>bskremovemap &lt;beatmap_id&gt;</code>", parse_mode="HTML")
        return

    beatmap_id = int(raw)
    from db.models.bsk_map_pool import BskMapPool
    async with get_db_session() as session:
        entry = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if not entry:
            await message.answer(f"Карта {beatmap_id} не найдена в пуле.")
            return
        entry.enabled = False
        await session.commit()

    await message.answer(f"Карта {beatmap_id} отключена из BSK пула.")


_BSK_POOL_PER_PAGE = 15


async def _bsk_pool_render(page: int) -> tuple[str, types.InlineKeyboardMarkup]:
    """Return (text, keyboard) for the given BSK pool page."""
    from db.models.bsk_map_pool import BskMapPool
    from sqlalchemy import func as sqlfunc

    async with get_db_session() as session:
        total = (await session.execute(
            select(sqlfunc.count()).select_from(BskMapPool)
        )).scalar() or 0
        enabled = (await session.execute(
            select(sqlfunc.count()).select_from(BskMapPool).where(BskMapPool.enabled == True)
        )).scalar() or 0

        maps = (await session.execute(
            select(BskMapPool)
            .order_by(BskMapPool.star_rating)
            .offset((page - 1) * _BSK_POOL_PER_PAGE)
            .limit(_BSK_POOL_PER_PAGE)
        )).scalars().all()

    pages = max(1, (total + _BSK_POOL_PER_PAGE - 1) // _BSK_POOL_PER_PAGE)
    page = max(1, min(page, pages))

    lines = [f"<b>BSK пул</b> — {enabled} активных / {total} всего  (стр. {page}/{pages})\n"]
    for m in maps:
        status = "✅" if m.enabled else "❌"
        lines.append(
            f"{status} <code>{m.beatmap_id}</code> {escape_html(m.artist)} - {escape_html(m.title)} "
            f"[{escape_html(m.version)}] ⭐{m.star_rating:.1f} {m.map_type or ''}"
        )

    nav = []
    if page > 1:
        nav.append(types.InlineKeyboardButton(text="◀", callback_data=f"bskpool:page:{page - 1}"))
    if page < pages:
        nav.append(types.InlineKeyboardButton(text="▶", callback_data=f"bskpool:page:{page + 1}"))
    kb = types.InlineKeyboardMarkup(inline_keyboard=[nav]) if nav else types.InlineKeyboardMarkup(inline_keyboard=[])

    return "\n".join(lines), kb


@router.message(TextTriggerFilter("bskpool", "bskp"))
async def cmd_bsk_pool(message: types.Message, trigger_args: TriggerArgs):
    """bskpool [page] — list BSK map pool."""
    args = (trigger_args.args or "").strip()
    page = max(1, int(args)) if args.isdigit() else 1
    text, kb = await _bsk_pool_render(page)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("bskpool:page:"))
async def on_bsk_pool_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[-1])
    text, kb = await _bsk_pool_render(page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.message(TextTriggerFilter("bskrecalc"))
async def cmd_bsk_recalc(message: types.Message):
    """Recalculate w_aim/w_speed/w_acc/w_cons and map_type for every map in the pool
    using stored bpm/ar/od/length values and the current _estimate_weights formula."""
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.map_pool import _estimate_weights
    from services.bsk.osu_parser import map_type_from_weights

    wait = await message.answer("Пересчитываю веса карт в пуле…")

    updated = 0
    type_counts: dict[str, int] = {}

    async with get_db_session() as session:
        maps = (await session.execute(select(BskMapPool))).scalars().all()
        for m in maps:
            weights = _estimate_weights(
                bpm=m.bpm or 0,
                ar=m.ar or 0,
                od=m.od or 0,
                length=m.length or 0,
            )
            m.w_aim   = weights["aim"]
            m.w_speed = weights["speed"]
            m.w_acc   = weights["acc"]
            m.w_cons  = weights["cons"]
            m.map_type = map_type_from_weights(weights)
            type_counts[m.map_type] = type_counts.get(m.map_type, 0) + 1
            updated += 1
        await session.commit()

    lines = [f"✅ Пересчитано карт: <b>{updated}</b>\n", "<b>Распределение по типам:</b>"]
    for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  • {t}: {cnt}")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("bskcleantest"))
async def cmd_bsk_clean_test(message: types.Message):
    """Delete all completed/cancelled/expired test duels and their rounds."""
    from db.models.bsk_duel import BskDuel
    from db.models.bsk_duel_round import BskDuelRound
    from sqlalchemy import delete as sa_delete

    wait = await message.answer("Удаляю тестовые дуэли…")

    async with get_db_session() as session:
        # Find all test duels in a terminal state
        test_duels = (await session.execute(
            select(BskDuel).where(
                BskDuel.is_test == True,
                BskDuel.status.in_(['completed', 'cancelled', 'expired']),
            )
        )).scalars().all()

        duel_ids = [d.id for d in test_duels]
        if not duel_ids:
            await wait.edit_text("Нет завершённых тестовых дуэлей для удаления.")
            return

        # Delete rounds first (FK constraint)
        rounds_del = await session.execute(
            sa_delete(BskDuelRound).where(BskDuelRound.duel_id.in_(duel_ids))
        )
        duels_del = await session.execute(
            sa_delete(BskDuel).where(BskDuel.id.in_(duel_ids))
        )
        await session.commit()

    await wait.edit_text(
        f"✅ Удалено тестовых дуэлей: <b>{duels_del.rowcount}</b>\n"
        f"Удалено раундов: <b>{rounds_del.rowcount}</b>",
        parse_mode="HTML",
    )


@router.message(TextTriggerFilter("bskimport"))
async def cmd_bsk_import_url(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    url = (trigger_args.args or "").strip()
    if not url or not url.startswith("http"):
        await message.answer(
            "Использование:\n"
            "• Файл .zip/.osz с подписью <code>bskimport</code>\n"
            "• <code>bskimport &lt;прямая ссылка&gt;</code>",
            parse_mode="HTML",
        )
        return

    if not (url.lower().endswith(".zip") or url.lower().endswith(".osz")):
        await message.answer("Ссылка должна вести на .zip или .osz файл.")
        return

    if len(_import_queue) >= MAX_IMPORT_SLOTS:
        await message.answer(f"Очередь импорта заполнена (макс. {MAX_IMPORT_SLOTS}). Подождите завершения текущих.")
        return

    wait = await message.answer("Скачиваю файл...")
    try:
        import aiohttp as _aiohttp
        async with _aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=_aiohttp.ClientTimeout(total=600)) as resp:
                if resp.status != 200:
                    await wait.edit_text(f"Не удалось скачать файл (HTTP {resp.status}).")
                    return
                if resp.content_length and resp.content_length > 1024 * 1024 * 1024:
                    await wait.edit_text("Файл слишком большой (макс. 1 GB).")
                    return
                zip_bytes = await resp.read()
    except Exception as e:
        await wait.edit_text(f"Ошибка при скачивании: {escape_html(str(e))}", parse_mode="HTML")
        return

    slot_id = _register_import(message.from_user.id, zip_bytes, url.split("/")[-1])
    osz_count, osu_count = _count_osu_files(zip_bytes)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Импортировать", callback_data=f"bskimport:confirm:{slot_id}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"bskimport:cancel:{slot_id}"),
    ]])
    await wait.edit_text(
        f"<b>Предпросмотр импорта</b>\n\n"
        f"Источник: <code>{escape_html(url.split('/')[-1])}</code>\n"
        f"Архивов .osz: <b>{osz_count}</b>\n"
        f"Карт .osu: <b>{osu_count}</b>\n"
        f"Слот: <b>{_queue_position(slot_id)}/{MAX_IMPORT_SLOTS}</b>\n\n"
        f"Подтвердить импорт в BSK пул?",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ─── Import queue ─────────────────────────────────────────────────────────────

MAX_IMPORT_SLOTS = 5
# slot_id -> {tg_id, zip_bytes, filename, status}
_import_queue: dict[str, dict] = {}
# Pending previews: admin_tg_id -> zip_bytes (legacy file upload path)
_pending_imports: dict[int, bytes] = {}


def _register_import(tg_id: int, zip_bytes: bytes, filename: str) -> str:
    import uuid
    slot_id = str(uuid.uuid4())[:8]
    _import_queue[slot_id] = {"tg_id": tg_id, "zip_bytes": zip_bytes,
                               "filename": filename, "status": "pending"}
    return slot_id


def _queue_position(slot_id: str) -> int:
    return list(_import_queue.keys()).index(slot_id) + 1 if slot_id in _import_queue else 0


def _count_osu_files(zip_bytes: bytes) -> tuple[int, int]:
    import zipfile as _zf
    osz_count = osu_count = 0
    try:
        with _zf.ZipFile(io.BytesIO(zip_bytes)) as outer:
            for name in outer.namelist():
                if name.lower().endswith(".osz"):
                    osz_count += 1
                    try:
                        with _zf.ZipFile(io.BytesIO(outer.read(name))) as inner:
                            osu_count += sum(1 for n in inner.namelist() if n.endswith(".osu"))
                    except Exception:
                        pass
                elif name.lower().endswith(".osu"):
                    osu_count += 1
    except _zf.BadZipFile:
        try:
            with _zf.ZipFile(io.BytesIO(zip_bytes)) as inner:
                osz_count = 1
                osu_count = sum(1 for n in inner.namelist() if n.endswith(".osu"))
        except Exception:
            pass
    return osz_count, osu_count


@router.callback_query(F.data.startswith("bskimport:"))
async def on_bsk_import_confirm(callback: types.CallbackQuery, osu_api_client):
    parts = callback.data.split(":")
    action = parts[1]
    slot_id = parts[2] if len(parts) > 2 else None

    # Legacy path (no slot_id) — old pending_imports dict
    if not slot_id:
        tg_id = callback.from_user.id
        if action == "cancel":
            _pending_imports.pop(tg_id, None)
            await callback.message.edit_text("Импорт отменён.")
            await callback.answer()
            return
        zip_bytes = _pending_imports.pop(tg_id, None)
        if not zip_bytes:
            await callback.answer("Сессия истекла. Загрузите файл заново.", show_alert=True)
            return
        slot_id = _register_import(tg_id, zip_bytes, "upload.zip")

    slot = _import_queue.get(slot_id)
    if not slot:
        await callback.answer("Сессия истекла. Загрузите файл заново.", show_alert=True)
        return

    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не ваш импорт.", show_alert=True)
        return

    if action == "cancel":
        _import_queue.pop(slot_id, None)
        await callback.message.edit_text("Импорт отменён.")
        await callback.answer()
        return

    # Confirm — run import in background
    slot["status"] = "running"
    await callback.message.edit_text(
        f"<b>Импортирую карты...</b>\n"
        f"Файл: <b>{escape_html(slot['filename'])}</b>",
        parse_mode="HTML",
    )
    await callback.answer()

    import asyncio
    msg = callback.message

    async def _run():
        try:
            from services.bsk.bulk_import import import_from_zip
            result = await import_from_zip(slot["zip_bytes"], osu_api_client)
        except Exception as e:
            logger.error(f"BSK bulk import error: {e}", exc_info=True)
            result = {"added": 0, "skipped": 0, "failed": 1, "errors": [str(e)]}
        finally:
            _import_queue.pop(slot_id, None)

        added, skipped, failed = result["added"], result["skipped"], result["failed"]
        lines = [
            "<b>BSK импорт завершён</b>",
            f"Файл: <b>{escape_html(slot['filename'])}</b>",
            f"✅ Добавлено: <b>{added}</b>",
            f"⏭ Пропущено: <b>{skipped}</b>",
            f"❌ Ошибок: <b>{failed}</b>",
        ]
        if result.get("errors"):
            lines.append("\nПервые ошибки:")
            for e in result["errors"]:
                lines.append(f"  • {escape_html(str(e)[:120])}")
        try:
            await msg.edit_text("\n".join(lines), parse_mode="HTML")
        except Exception:
            pass

    asyncio.create_task(_run())


@router.message(F.document & (F.caption.lower() == "bskimport"))
async def cmd_bsk_bulk_import(message: types.Message, osu_api_client):
    doc = message.document
    fname = (doc.file_name or "").lower()
    if not (fname.endswith(".zip") or fname.endswith(".osz")):
        await message.answer("Поддерживаются только файлы <b>.zip</b> или <b>.osz</b>.", parse_mode="HTML")
        return

    if len(_import_queue) >= MAX_IMPORT_SLOTS:
        await message.answer(f"Очередь импорта заполнена (макс. {MAX_IMPORT_SLOTS}). Подождите завершения текущих.")
        return

    if doc.file_size and doc.file_size > 1024 * 1024 * 1024:
        await message.answer("Файл слишком большой (макс. 1 GB).")
        return

    wait = await message.answer("Скачиваю файл...")
    try:
        import aiohttp as _aiohttp
        from config.settings import TELEGRAM_BOT_TOKEN
        file = await message.bot.get_file(doc.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file.file_path}"
        async with _aiohttp.ClientSession() as sess:
            async with sess.get(file_url, timeout=_aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status != 200:
                    await wait.edit_text(f"Не удалось скачать файл (HTTP {resp.status}).")
                    return
                zip_bytes = await resp.read()
    except Exception as e:
        await wait.edit_text(f"Не удалось скачать файл: {e}")
        return

    slot_id = _register_import(message.from_user.id, zip_bytes, doc.file_name or "upload.zip")
    osz_count, osu_count = _count_osu_files(zip_bytes)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Импортировать", callback_data=f"bskimport:confirm:{slot_id}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"bskimport:cancel:{slot_id}"),
    ]])
    await wait.edit_text(
        f"<b>Предпросмотр импорта</b>\n\n"
        f"Файл: <b>{escape_html(doc.file_name)}</b>\n"
        f"Архивов .osz: <b>{osz_count}</b>\n"
        f"Карт .osu: <b>{osu_count}</b>\n"
        f"Слот: <b>{_queue_position(slot_id)}/{MAX_IMPORT_SLOTS}</b>\n\n"
        f"Подтвердить импорт в BSK пул?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.message(TextTriggerFilter("bskimportqueue", "bskiq"))
async def cmd_bsk_import_queue(message: types.Message):
    if not _import_queue:
        await message.answer("Очередь импорта пуста.")
        return
    lines = ["<b>Очередь импорта BSK</b>\n"]
    for i, (sid, slot) in enumerate(_import_queue.items(), 1):
        status = slot["status"]
        fname = escape_html(slot["filename"])
        icon = "⏳" if status == "pending" else "🔄"
        lines.append(f"{icon} {i}. <b>{fname}</b> [{status}]")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("bsktest"))
async def cmd_bsk_test(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """bsktest [casual|ranked] — start a test duel as both players."""
    args = (trigger_args.args or "").strip().lower()
    mode = "casual" if args not in ("ranked",) else "ranked"

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

    from services.bsk.duel_manager import create_test_duel
    duel = await create_test_duel(
        bot=message.bot,
        chat_id=message.chat.id,
        user_id=user.id,
        mode=mode,
        osu_api=osu_api_client,
    )
    if not duel:
        await message.answer("Не удалось создать тестовую дуэль. Убедитесь что в пуле есть карты.")


@router.message(TextTriggerFilter("bsktestround", "bsktr"))
async def cmd_bsk_test_round(message: types.Message, trigger_args: TriggerArgs):
    """bsktestround [p1_pp p1_acc p2_pp p2_acc] — simulate round with fake scores."""
    args = (trigger_args.args or "").strip().split()

    # Defaults
    p1_pp, p1_acc, p2_pp, p2_acc = 300.0, 97.5, 280.0, 96.0
    try:
        if len(args) >= 4:
            p1_pp, p1_acc, p2_pp, p2_acc = float(args[0]), float(args[1]), float(args[2]), float(args[3])
        elif len(args) == 2:
            p1_pp, p2_pp = float(args[0]), float(args[1])
    except ValueError:
        await message.answer("Использование: <code>bsktestround [p1_pp p1_acc p2_pp p2_acc]</code>", parse_mode="HTML")
        return

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        from sqlalchemy import select as _select
        from db.models.bsk_duel import BskDuel as _BskDuel
        duel = (await session.execute(
            _select(_BskDuel).where(
                _BskDuel.is_test == True,
                _BskDuel.status == 'round_active',
                (_BskDuel.player1_user_id == user.id) | (_BskDuel.player2_user_id == user.id),
            )
        )).scalar_one_or_none()

    if not duel:
        await message.answer("Нет активной тестовой дуэли. Запустите <code>bsktest</code>.", parse_mode="HTML")
        return

    from services.bsk.duel_manager import simulate_test_round
    ok = await simulate_test_round(
        bot=message.bot,
        duel_id=duel.id,
        p1_pp=p1_pp, p1_acc=p1_acc, p1_combo_ratio=0.95, p1_misses=1,
        p2_pp=p2_pp, p2_acc=p2_acc, p2_combo_ratio=0.90, p2_misses=2,
    )
    if not ok:
        await message.answer("Не удалось симулировать раунд.")


@router.message(TextTriggerFilter("bsktestend", "bskte"))
async def cmd_bsk_test_end(message: types.Message):
    """bsktestend — cancel active test duel."""
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        from sqlalchemy import select as _select
        from db.models.bsk_duel import BskDuel as _BskDuel
        duel = (await session.execute(
            _select(_BskDuel).where(
                _BskDuel.is_test == True,
                _BskDuel.status.in_(['pending', 'accepted', 'round_active']),
                (_BskDuel.player1_user_id == user.id) | (_BskDuel.player2_user_id == user.id),
            )
        )).scalar_one_or_none()

        if not duel:
            await message.answer("Нет активной тестовой дуэли.")
            return

        duel.status = 'cancelled'
        await session.commit()

    await message.answer("Тестовая дуэль отменена.")


def _ml_monitor_keyboard(running: bool, paused: bool) -> types.InlineKeyboardMarkup:
    if not running:
        return types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="🔄 Запустить снова", callback_data="bskml:start"),
        ]])
    if paused:
        return types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="▶️ Продолжить", callback_data="bskml:resume"),
            types.InlineKeyboardButton(text="❌ Отменить", callback_data="bskml:cancel"),
        ]])
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="⏸ Пауза", callback_data="bskml:pause"),
        types.InlineKeyboardButton(text="❌ Отменить", callback_data="bskml:cancel"),
        types.InlineKeyboardButton(text="🔃 Обновить", callback_data="bskml:refresh"),
    ]])


@router.message(TextTriggerFilter("bsktrainml"))
async def cmd_bsk_train_ml(message: types.Message):
    from tasks.bsk_ml_trainer import run_nightly_training, is_running, get_progress

    if is_running():
        await message.answer("Обучение уже запущено. Используйте <code>bskmlmonitor</code> для наблюдения.", parse_mode="HTML")
        return

    import asyncio
    wait = await message.answer(
        "<b>ML обучение запущено...</b>",
        parse_mode="HTML",
        reply_markup=_ml_monitor_keyboard(True, False),
    )

    async def _run_and_update():
        from tasks.bsk_ml_trainer import run_nightly_training, is_paused
        result = await run_nightly_training(triggered_by=f"admin:{message.from_user.id}")
        status = result.get("status", "?")
        if status == "skipped":
            text = f"Недостаточно данных.\nРаундов: <b>{result.get('rounds_used', 0)}</b> (нужно ≥50)"
        elif status == "ok":
            text = (f"<b>ML обучение завершено</b>\n\n"
                    f"Раундов: <b>{result.get('rounds_used', 0)}</b>\n"
                    f"Карт обновлено: <b>{result.get('maps_updated', 0)}</b>\n"
                    f"Карт пропущено: <b>{result.get('maps_skipped', 0)}</b>")
        elif status == "cancelled":
            text = f"<b>Обучение отменено.</b>\nКарт обновлено до отмены: <b>{result.get('maps_updated', 0)}</b>"
        elif status == "timeout":
            text = f"Обучение прервано по таймауту (3 часа).\nОбновлено: <b>{result.get('maps_updated', 0)}</b>"
        else:
            text = f"Ошибка: {result.get('error', '?')}"
        try:
            await wait.edit_text(text, parse_mode="HTML", reply_markup=_ml_monitor_keyboard(False, False))
        except Exception:
            pass

    asyncio.create_task(_run_and_update())


@router.message(TextTriggerFilter("bskmlmonitor", "bskmlm"))
async def cmd_bsk_ml_monitor(message: types.Message):
    from tasks.bsk_ml_trainer import is_running, is_paused, get_progress

    if not is_running():
        await message.answer("Модель в данный момент не обучается.")
        return

    p = get_progress()
    paused = is_paused()
    status_text = "на паузе" if paused else "идёт"
    done = p.get("maps_done", 0)
    total = p.get("maps_total", "?")
    updated = p.get("maps_updated", 0)
    skipped = p.get("maps_skipped", 0)
    rounds = p.get("rounds_used", 0)

    await message.answer(
        f"<b>ML обучение {status_text}</b>\n\n"
        f"Раундов: <b>{rounds}</b>\n"
        f"Прогресс: <b>{done}/{total}</b> карт\n"
        f"Обновлено: <b>{updated}</b>  Пропущено: <b>{skipped}</b>",
        parse_mode="HTML",
        reply_markup=_ml_monitor_keyboard(True, paused),
    )


@router.callback_query(F.data.startswith("bskml:"))
async def on_bskml_control(callback: types.CallbackQuery):
    from tasks.bsk_ml_trainer import (
        is_running, is_paused, pause_training, resume_training,
        cancel_training, get_progress, run_nightly_training
    )
    action = callback.data.split(":")[1]

    if action == "pause":
        if is_running() and not is_paused():
            pause_training()
            await callback.answer("Пауза")
            p = get_progress()
            await callback.message.edit_text(
                f"<b>ML обучение на паузе</b>\n\n"
                f"Прогресс: <b>{p.get('maps_done', 0)}/{p.get('maps_total', '?')}</b> карт\n"
                f"Обновлено: <b>{p.get('maps_updated', 0)}</b>",
                parse_mode="HTML",
                reply_markup=_ml_monitor_keyboard(True, True),
            )
        else:
            await callback.answer("Нечего ставить на паузу.", show_alert=True)

    elif action == "resume":
        if is_paused():
            resume_training()
            await callback.answer("Продолжаю")
            p = get_progress()
            await callback.message.edit_text(
                f"<b>ML обучение продолжается...</b>\n\n"
                f"Прогресс: <b>{p.get('maps_done', 0)}/{p.get('maps_total', '?')}</b> карт",
                parse_mode="HTML",
                reply_markup=_ml_monitor_keyboard(True, False),
            )
        else:
            await callback.answer("Обучение не на паузе.", show_alert=True)

    elif action == "cancel":
        if is_running():
            cancel_training()
            await callback.answer("Отменяю...")
            await callback.message.edit_text(
                "<b>Обучение отменено.</b>",
                parse_mode="HTML",
                reply_markup=_ml_monitor_keyboard(False, False),
            )
        else:
            await callback.answer("Обучение не запущено.", show_alert=True)

    elif action == "refresh":
        if is_running():
            p = get_progress()
            status_text = "на паузе" if is_paused() else "идёт"
            await callback.answer("Обновлено")
            await callback.message.edit_text(
                f"<b>ML обучение {status_text}</b>\n\n"
                f"Прогресс: <b>{p.get('maps_done', 0)}/{p.get('maps_total', '?')}</b> карт\n"
                f"Обновлено: <b>{p.get('maps_updated', 0)}</b>\n"
                f"Пропущено: <b>{p.get('maps_skipped', 0)}</b>",
                parse_mode="HTML",
                reply_markup=_ml_monitor_keyboard(True, is_paused()),
            )
        else:
            await callback.answer("Обучение завершено.", show_alert=True)

    elif action == "start":
        if is_running():
            await callback.answer("Уже запущено.", show_alert=True)
            return
        await callback.answer("Запускаю...")
        import asyncio
        async def _run():
            result = await run_nightly_training(triggered_by=f"admin:{callback.from_user.id}")
            status = result.get("status", "?")
            text = (f"<b>ML завершено</b> — {status}\n"
                    f"Обновлено: <b>{result.get('maps_updated', 0)}</b>")
            try:
                await callback.message.edit_text(text, parse_mode="HTML",
                                                 reply_markup=_ml_monitor_keyboard(False, False))
            except Exception:
                pass
        asyncio.create_task(_run())
        await callback.message.edit_text(
            "<b>ML обучение запущено...</b>",
            parse_mode="HTML",
            reply_markup=_ml_monitor_keyboard(True, False),
        )



@router.message(TextTriggerFilter("bskmlstats"))
async def cmd_bsk_ml_stats(message: types.Message):
    """bskmlstats — show BSK ML training history."""
    from db.models.bsk_ml_run import BskMlRun
    from db.models.bsk_duel_round import BskDuelRound
    from sqlalchemy import func as sqlfunc, desc
    from datetime import datetime, timezone, timedelta

    async with get_db_session() as session:
        runs = (await session.execute(
            select(BskMlRun).order_by(desc(BskMlRun.ran_at)).limit(5)
        )).scalars().all()

        total_rounds = (await session.execute(
            select(sqlfunc.count()).select_from(BskDuelRound).where(
                BskDuelRound.status == "completed",
                BskDuelRound.player1_composite.isnot(None),
            )
        )).scalar() or 0

    # Next scheduled run
    now = datetime.now()
    next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if now >= next_run:
        next_run += timedelta(days=1)
    hours_until = (next_run - now).total_seconds() / 3600

    lines = [
        "<b>BSK ML — статистика</b>\n",
        f"Раундов в БД: <b>{total_rounds}</b> (нужно ≥50 для обучения)",
        f"Следующий запуск: <b>{next_run.strftime('%d.%m %H:%M')}</b> (через {hours_until:.1f}ч)\n",
    ]

    if runs:
        lines.append("<b>Последние запуски:</b>")
        for r in runs:
            ts = r.ran_at.strftime("%d.%m %H:%M") if r.ran_at else "?"
            trigger = r.triggered_by or "scheduler"
            acc_str = ""
            if r.prediction_accuracy is not None:
                acc_str = f"  ·  🎯 {r.prediction_accuracy*100:.1f}% ({r.predictions_correct}/{r.predictions_total})"
            if r.status == "ok":
                lines.append(f"✅ {ts} [{trigger}] — обновлено {r.maps_updated} карт из {r.rounds_used} раундов{acc_str}")
            elif r.status == "skipped":
                lines.append(f"⏭ {ts} [{trigger}] — мало данных ({r.rounds_used} раундов)")
            elif r.status == "timeout":
                lines.append(f"⏰ {ts} [{trigger}] — таймаут{acc_str}")
            else:
                lines.append(f"❌ {ts} [{trigger}] — ошибка: {r.notes or '?'}")
    else:
        lines.append("Запусков ещё не было.")

    await message.answer("\n".join(lines), parse_mode="HTML")


from bot.handlers.admin.review import *  # noqa: F401,F403


