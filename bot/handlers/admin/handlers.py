from datetime import datetime, timedelta
from uuid import uuid4
import io

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func, desc

from bot.filters import TextTriggerFilter, TriggerArgs

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from utils.admin_check import AdminFilter
from utils.osu.helpers import extract_beatmap_id
from utils.osu.resolve_user import get_any_user_by_telegram_id
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


@router.message(TextTriggerFilter("whois"))
async def cmd_whois(message: types.Message, trigger_args: TriggerArgs):
    """whois <user_id_or_tg_id> — show user info by internal User.id or telegram_id.

    Useful when the OAuth/token logs print '_id=N' and you need to figure out
    who that is and how to message them.
    """
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.lstrip("-").isdigit():
        await message.answer(
            "Использование: <code>whois &lt;user_id или telegram_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    target = int(raw)
    from db.models.oauth_token import OAuthToken

    async with get_db_session() as session:
        # Try by User.id first; fall back to telegram_id (large positive numbers)
        user = (await session.execute(
            select(User).where(User.id == target)
        )).scalar_one_or_none()
        if not user:
            user = (await session.execute(
                select(User).where(User.telegram_id == target)
            )).scalar_one_or_none()

        if not user:
            await message.answer(f"Пользователь с id={target} не найден ни в User.id, ни в telegram_id.")
            return

        token = (await session.execute(
            select(OAuthToken).where(OAuthToken.user_id == user.id)
        )).scalar_one_or_none()

    last_seen = user.last_seen.strftime("%Y-%m-%d %H:%M") if getattr(user, "last_seen", None) else "—"
    if token:
        exp = token.token_expiry.strftime("%Y-%m-%d %H:%M") if token.token_expiry else "—"
        oauth_line = f"✅ Привязан, истекает: <code>{exp}</code>"
    else:
        oauth_line = "❌ <b>Нет токена</b> — нужен relink"

    text = (
        f"<b>User.id:</b>      <code>{user.id}</code>\n"
        f"<b>telegram_id:</b>  <code>{user.telegram_id}</code>\n"
        f"<b>osu! ник:</b>     <b>{escape_html(user.osu_username or '—')}</b> "
        f"(osu_id <code>{user.osu_user_id or '—'}</code>)\n"
        f"<b>OAuth:</b>        {oauth_line}\n"
        f"<b>Last seen:</b>    <code>{last_seen}</code>\n\n"
        f"📨 Написать: <a href=\"tg://user?id={user.telegram_id}\">открыть профиль</a>\n"
        f"🔁 Прислать DM с просьбой relink: <code>notifyrelink {user.id}</code>"
    )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(TextTriggerFilter("notifyrelink"))
async def cmd_notify_relink(message: types.Message, trigger_args: TriggerArgs):
    """notifyrelink <user_id_or_tg_id> — DM the user and ask them to re-link osu!.

    Used after OAuth permanent failures (bsk-ml token_manager logs
    'Refresh token rejected for user_id=N — deleting row, user must re-link').
    """
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.lstrip("-").isdigit():
        await message.answer(
            "Использование: <code>notifyrelink &lt;user_id или telegram_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    target = int(raw)

    async with get_db_session() as session:
        user = (await session.execute(
            select(User).where(User.id == target)
        )).scalar_one_or_none()
        if not user:
            user = (await session.execute(
                select(User).where(User.telegram_id == target)
            )).scalar_one_or_none()
        if not user:
            await message.answer(f"Пользователь с id={target} не найден.")
            return
        if not user.telegram_id:
            await message.answer(f"У {user.osu_username} нет telegram_id — невозможно написать в личку.")
            return

    dm_text = (
        f"⚠️ <b>Привязка osu! аккаунта истекла</b>\n\n"
        f"Привет, <b>{escape_html(user.osu_username)}</b>! "
        f"Похоже, твой osu! токен был отозван (например, ты разлогинился на osu.ppy.sh "
        f"или сменил пароль), и бот больше не может получать твои скоры.\n\n"
        f"Перепривяжи аккаунт командой:\n"
        f"<code>relink</code>\n\n"
        f"Бот пришлёт ссылку для авторизации в osu!. "
        f"<b>Прогресс, рейтинги и история сохранятся</b> — это не unlink, "
        f"всё что было — останется. После этого всё снова заработает: дуэли, "
        f"профиль, recent."
    )

    try:
        await message.bot.send_message(
            user.telegram_id, dm_text, parse_mode="HTML", disable_web_page_preview=True,
        )
        await message.answer(
            f"✅ DM отправлен <b>{escape_html(user.osu_username)}</b> "
            f"(tg <code>{user.telegram_id}</code>).",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось написать в личку <b>{escape_html(user.osu_username)}</b>: "
            f"<code>{escape_html(str(e))}</code>\n\n"
            f"Скорее всего, пользователь не начинал диалог с ботом или заблокировал его. "
            f"Напиши вручную: <a href=\"tg://user?id={user.telegram_id}\">открыть профиль</a>",
            parse_mode="HTML",
        )


@router.message(TextTriggerFilter("whereami"))
async def cmd_whereami(message: types.Message):
    """whereami — print chat_id and message_thread_id of the current location.

    Useful for picking the value to set in BSK_DUEL_THREAD_ID env var.
    """
    chat_id   = message.chat.id
    thread_id = message.message_thread_id
    is_topic  = bool(getattr(message, "is_topic_message", False))
    lines = [
        f"<b>chat_id:</b>          <code>{chat_id}</code>",
        f"<b>message_thread_id:</b> <code>{thread_id if thread_id is not None else '— (General / non-forum)'}</code>",
        f"<b>is_topic_message:</b>  <code>{is_topic}</code>",
    ]
    if thread_id is not None:
        lines.append(
            f"\nЧтобы дуэли всегда публиковались сюда, добавь в <code>.env</code>:\n"
            f"<code>BSK_DUEL_THREAD_ID={thread_id}</code>"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("bskenable"))
async def cmd_bsk_enable_map(message: types.Message, trigger_args: TriggerArgs):
    """bskenable <beatmap_id> — re-enable a previously disabled BSK pool map."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer(
            "Использование: <code>bskenable &lt;beatmap_id&gt;</code>",
            parse_mode="HTML",
        )
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
        was_enabled = entry.enabled
        entry.enabled = True
        await session.commit()

    if was_enabled:
        await message.answer(f"Карта {beatmap_id} уже была включена.")
    else:
        await message.answer(f"✅ Карта {beatmap_id} снова в пуле.")


_BSK_BROKEN_PER_PAGE = 15


async def _bsk_broken_collect() -> tuple[
    list[tuple["BskMapPool", list[str]]],  # type: ignore  # noqa: F821
    list["BskMapPool"],                     # type: ignore  # noqa: F821
]:
    """Scan the pool and split entries into (broken, disabled-but-clean)."""
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.map_pool import map_is_broken
    from sqlalchemy import or_

    async with get_db_session() as session:
        candidates = (await session.execute(
            select(BskMapPool).where(
                or_(
                    BskMapPool.star_rating <= 0,
                    BskMapPool.api_aim_diff.is_(None),
                    BskMapPool.f_note_count.is_(None),
                    BskMapPool.enabled == False,  # noqa: E712
                )
            ).order_by(BskMapPool.star_rating)
        )).scalars().all()

    broken: list[tuple[BskMapPool, list[str]]] = []
    disabled_only: list[BskMapPool] = []
    for m in candidates:
        is_b, reasons = map_is_broken(m)
        if is_b:
            broken.append((m, reasons))
        elif not m.enabled:
            disabled_only.append(m)
    return broken, disabled_only


async def _bsk_broken_render(
    page: int, section: str = "broken"
) -> tuple[str, types.InlineKeyboardMarkup]:
    """Render one page of `bskbroken` for the given section ('broken'|'disabled')."""
    broken, disabled_only = await _bsk_broken_collect()

    if section not in ("broken", "disabled"):
        section = "broken"

    items_broken = broken
    items_disabled = disabled_only

    if section == "broken":
        items: list = items_broken
        per = _BSK_BROKEN_PER_PAGE
        header_emoji = "⚠️"
        header_label = "Битые карты"
    else:
        items = items_disabled
        per = _BSK_BROKEN_PER_PAGE
        header_emoji = "❌"
        header_label = "Отключённые, но целые"

    total_items = len(items)
    pages = max(1, (total_items + per - 1) // per)
    page = max(1, min(page, pages))
    start = (page - 1) * per
    chunk = items[start:start + per]

    lines = [
        "<b>BSK — диагностика пула</b>",
        f"⚠️ Битых: <b>{len(items_broken)}</b>   "
        f"❌ Отключённых: <b>{len(items_disabled)}</b>",
        "",
        f"{header_emoji} <b>{header_label} ({total_items}):</b>"
        + (f"  стр. {page}/{pages}" if total_items else ""),
    ]

    if not chunk:
        lines.append("<i>— пусто —</i>")
    else:
        if section == "broken":
            for m, reasons in chunk:
                tag = ", ".join(reasons)
                lines.append(
                    f"<code>{m.beatmap_id}</code> {escape_html(m.artist)} - "
                    f"{escape_html(m.title)} [{escape_html(m.version)}] · {tag}"
                )
        else:
            for m in chunk:
                lines.append(
                    f"<code>{m.beatmap_id}</code> {escape_html(m.artist)} - "
                    f"{escape_html(m.title)} [{escape_html(m.version)}]"
                )

    if section == "broken" and items_broken:
        lines += [
            "",
            "Чинить: <code>bskrefresh &lt;id&gt;</code> "
            "или <code>bskrefresh broken</code> для пакетной починки.",
        ]
    elif section == "disabled" and items_disabled:
        lines += ["", "Включить: <code>bskenable &lt;id&gt;</code>"]

    # ── Keyboard ─────────────────────────────────────────────────────────────
    nav_row: list[types.InlineKeyboardButton] = []
    if pages > 1:
        if page > 1:
            nav_row.append(types.InlineKeyboardButton(
                text="◀", callback_data=f"bskbroken:page:{section}:{page - 1}"
            ))
        nav_row.append(types.InlineKeyboardButton(
            text=f"{page}/{pages}", callback_data="bskbroken:noop"
        ))
        if page < pages:
            nav_row.append(types.InlineKeyboardButton(
                text="▶", callback_data=f"bskbroken:page:{section}:{page + 1}"
            ))

    other = "disabled" if section == "broken" else "broken"
    other_count = len(items_disabled) if section == "broken" else len(items_broken)
    other_label = (
        f"❌ Отключённые ({other_count})"
        if section == "broken" else f"⚠️ Битые ({other_count})"
    )
    switch_row = [types.InlineKeyboardButton(
        text=other_label, callback_data=f"bskbroken:page:{other}:1"
    )]

    rows: list[list[types.InlineKeyboardButton]] = []
    if nav_row:
        rows.append(nav_row)
    rows.append(switch_row)
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)

    return "\n".join(lines), kb


@router.message(TextTriggerFilter("bskbroken"))
async def cmd_bsk_broken(message: types.Message, trigger_args: TriggerArgs):
    """bskbroken [page] — list broken / disabled BSK pool maps with pagination."""
    args = (trigger_args.args or "").strip().lower()
    section = "broken"
    page = 1
    if args:
        for token in args.split():
            if token in ("broken", "disabled"):
                section = token
            elif token.isdigit():
                page = max(1, int(token))

    broken, disabled_only = await _bsk_broken_collect()
    if not broken and not disabled_only:
        await message.answer("✅ В пуле нет карт с проблемами.")
        return

    text, kb = await _bsk_broken_render(page, section)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("bskbroken:"))
async def on_bsk_broken_callback(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) >= 2 and parts[1] == "noop":
        await callback.answer()
        return
    # Format: bskbroken:page:<section>:<n>
    if len(parts) >= 4 and parts[1] == "page":
        section = parts[2]
        try:
            page = int(parts[3])
        except ValueError:
            page = 1
        text, kb = await _bsk_broken_render(page, section)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            logger.debug("bskbroken: edit_text failed", exc_info=True)
        await callback.answer()
        return
    await callback.answer()


# ── Post-refresh actions ─────────────────────────────────────────────────────
# After a `bskrefresh broken` batch, give the admin a chance to disable or
# delete the maps that are still broken, instead of leaving them dangling.

# slot_id -> {tg_id: int, bad_ids: list[int], created_at: datetime}
_refresh_slots: dict[str, dict] = {}


def _register_refresh_slot(tg_id: int, bad_ids: list[int]) -> str:
    """Stash the post-refresh bad_ids list under a short slot id."""
    slot_id = uuid4().hex[:8]
    _refresh_slots[slot_id] = {
        "tg_id": tg_id,
        "bad_ids": list(bad_ids),
        "created_at": datetime.utcnow(),
    }
    # Lazy cleanup: drop slots older than 1h to avoid unbounded growth.
    cutoff = datetime.utcnow() - timedelta(hours=1)
    for sid, data in list(_refresh_slots.items()):
        if data.get("created_at") and data["created_at"] < cutoff:
            _refresh_slots.pop(sid, None)
    return slot_id


@router.callback_query(F.data.startswith("bskrefresh:fix:"))
async def on_bsk_refresh_fix(callback: types.CallbackQuery):
    """Handle 'disable / delete / cancel' actions for the post-refresh prompt."""
    from db.models.bsk_map_pool import BskMapPool

    parts = callback.data.split(":")
    # bskrefresh:fix:<action>:<slot>
    if len(parts) != 4:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    action = parts[2]
    slot_id = parts[3]

    slot = _refresh_slots.get(slot_id)
    if not slot:
        await callback.answer("Сессия истекла — запусти bskrefresh broken заново.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("bskrefresh:fix expired slot — edit_reply_markup failed", exc_info=True)
        return

    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не твой запрос.", show_alert=True)
        return

    bad_ids: list[int] = slot["bad_ids"]

    if action == "cancel":
        _refresh_slots.pop(slot_id, None)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("bskrefresh:fix cancel — edit_reply_markup failed", exc_info=True)
        await callback.answer("Оставлено как есть.")
        return

    if action not in ("disable", "delete"):
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    if not bad_ids:
        _refresh_slots.pop(slot_id, None)
        await callback.answer("Нечего обрабатывать — список пуст.", show_alert=True)
        return

    # ── Apply the action ────────────────────────────────────────────────────
    affected = 0
    async with get_db_session() as session:
        rows = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id.in_(bad_ids))
        )).scalars().all()
        if action == "disable":
            for entry in rows:
                if entry.enabled:
                    entry.enabled = False
                    affected += 1
        else:  # delete
            for entry in rows:
                await session.delete(entry)
                affected += 1
        await session.commit()

    _refresh_slots.pop(slot_id, None)

    verb = "отключено" if action == "disable" else "удалено"
    suffix_lines = [
        "",
        f"<b>Действие применено:</b> {verb} <b>{affected}</b> карт.",
    ]
    new_text = (callback.message.html_text or callback.message.text or "") + "\n" + "\n".join(suffix_lines)
    try:
        await callback.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
    except Exception:
        # Fallback: just drop the keyboard and post a follow-up.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("bskrefresh:fix — edit_reply_markup failed", exc_info=True)
        await callback.message.answer("\n".join(suffix_lines), parse_mode="HTML")
    logger.info(f"bskrefresh:fix admin={callback.from_user.id} action={action} affected={affected}")
    await callback.answer(f"{verb}: {affected}")


@router.message(TextTriggerFilter("bskrefresh"))
async def cmd_bsk_refresh(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """
    bskrefresh <beatmap_id>   — re-pull a single map from the osu! API + CDN.
    bskrefresh broken          — refresh every map flagged by bskbroken.
    bskrefresh disabled        — re-enable every disabled map and re-pull.

    Useful when maps were imported while the API was misbehaving and ended
    up with star_rating=0, missing parsed features, etc. Retries each
    network call up to 3 times before giving up.
    """
    raw = (trigger_args.args or "").strip().lower()
    if not raw:
        await message.answer(
            "Использование:\n"
            "<code>bskrefresh &lt;id&gt;</code> — одна карта\n"
            "<code>bskrefresh broken</code> — все битые\n"
            "<code>bskrefresh disabled</code> — все отключённые",
            parse_mode="HTML",
        )
        return

    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.map_pool import refresh_map, map_is_broken
    from sqlalchemy import or_
    import asyncio

    # ── Single-map mode ──────────────────────────────────────────────────────
    if raw.isdigit():
        beatmap_id = int(raw)
        wait = await message.answer(f"🔄 Обновляю карту {beatmap_id}…")
        result = await refresh_map(osu_api_client, beatmap_id, re_enable=True)

        st = result["status"]
        emoji = {"ok": "✅", "partial": "⚠️", "no_data": "❌", "not_found": "🚫", "error": "❌"}.get(st, "❓")
        reasons = ", ".join(result["reasons"]) or "—"
        updated = ", ".join(result["updated"]) or "—"
        text = (
            f"{emoji} <b>Карта {beatmap_id}</b>: {result['message']}\n\n"
            f"Было битым: <code>{reasons}</code>\n"
            f"Обновлено:  <code>{updated}</code>"
        )
        try:
            await wait.edit_text(text, parse_mode="HTML")
        except Exception:
            await message.answer(text, parse_mode="HTML")
        return

    # ── Batch modes ──────────────────────────────────────────────────────────
    if raw not in ("broken", "disabled"):
        await message.answer("Неизвестный режим. Доступно: <id>, broken, disabled", parse_mode="HTML")
        return

    async with get_db_session() as session:
        if raw == "disabled":
            candidates = (await session.execute(
                select(BskMapPool).where(BskMapPool.enabled == False)
            )).scalars().all()
        else:  # broken
            candidates = (await session.execute(
                select(BskMapPool).where(
                    or_(
                        BskMapPool.star_rating <= 0,
                        BskMapPool.api_aim_diff.is_(None),
                        BskMapPool.f_note_count.is_(None),
                    )
                )
            )).scalars().all()

    if raw == "broken":
        # Verify against map_is_broken (the SQL filter is permissive).
        candidates = [m for m, in [(m,) for m in candidates] if map_is_broken(m)[0]]

    if not candidates:
        await message.answer("Нечего обновлять — пул чистый.")
        return

    wait = await message.answer(f"🔄 Обновляю {len(candidates)} карт…")

    counts = {"ok": 0, "partial": 0, "no_data": 0, "not_found": 0, "error": 0}
    bad_ids: list[int] = []

    for idx, m in enumerate(candidates, 1):
        if idx % 10 == 0:
            try:
                await wait.edit_text(
                    f"🔄 {idx}/{len(candidates)}…\n"
                    f"✅ {counts['ok']}  ⚠️ {counts['partial']}  ❌ {counts['no_data'] + counts['error']}"
                )
            except Exception:
                logger.debug("bskrefresh: progress edit_text failed", exc_info=True)
        try:
            r = await refresh_map(osu_api_client, m.beatmap_id, re_enable=True)
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            if r["status"] in ("no_data", "not_found", "error", "partial"):
                bad_ids.append(m.beatmap_id)
        except Exception as e:
            logger.error(f"bskrefresh batch error for {m.beatmap_id}: {e}", exc_info=True)
            counts["error"] += 1
            bad_ids.append(m.beatmap_id)
        await asyncio.sleep(0.2)

    text_lines = [
        "<b>Обновление завершено</b>\n",
        f"✅ Полностью:  <b>{counts['ok']}</b>",
        f"⚠️ Частично:    <b>{counts['partial']}</b>",
        f"❌ Без данных: <b>{counts['no_data']}</b>",
        f"🚫 Не найдено: <b>{counts['not_found']}</b>",
        f"❌ Ошибок:      <b>{counts['error']}</b>",
    ]

    kb: types.InlineKeyboardMarkup | None = None
    if bad_ids:
        sample = ", ".join(f"<code>{i}</code>" for i in bad_ids[:10])
        more = f" (+{len(bad_ids) - 10})" if len(bad_ids) > 10 else ""
        text_lines.append(f"\nПроблемные ({len(bad_ids)}): {sample}{more}")
        text_lines.append(
            "\nЧто сделать с картами, которые остались битыми?"
        )
        slot = _register_refresh_slot(message.from_user.id, bad_ids)
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"❌ Отключить {len(bad_ids)}",
                    callback_data=f"bskrefresh:fix:disable:{slot}",
                ),
                types.InlineKeyboardButton(
                    text=f"🗑 Удалить {len(bad_ids)}",
                    callback_data=f"bskrefresh:fix:delete:{slot}",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="Оставить как есть",
                    callback_data=f"bskrefresh:fix:cancel:{slot}",
                ),
            ],
        ])

    try:
        await wait.edit_text("\n".join(text_lines), parse_mode="HTML", reply_markup=kb)
    except Exception:
        await message.answer("\n".join(text_lines), parse_mode="HTML", reply_markup=kb)


_BSK_POOL_PER_PAGE = 15


async def _bsk_pool_render(page: int) -> tuple[str, types.InlineKeyboardMarkup]:
    """Return (text, keyboard) for the given BSK pool page."""
    from db.models.bsk_map_pool import BskMapPool

    async with get_db_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(BskMapPool)
        )).scalar() or 0
        enabled = (await session.execute(
            select(func.count()).select_from(BskMapPool).where(BskMapPool.enabled == True)
        )).scalar() or 0

        maps = (await session.execute(
            select(BskMapPool)
            .order_by(BskMapPool.star_rating)
            .offset((page - 1) * _BSK_POOL_PER_PAGE)
            .limit(_BSK_POOL_PER_PAGE)
        )).scalars().all()

    pages = max(1, (total + _BSK_POOL_PER_PAGE - 1) // _BSK_POOL_PER_PAGE)
    page = max(1, min(page, pages))

    from services.bsk.map_pool import map_is_broken
    lines = [f"<b>BSK пул</b> — {enabled} активных / {total} всего  (стр. {page}/{pages})\n"]
    for m in maps:
        broken, _ = map_is_broken(m)
        if not m.enabled:
            status = "❌"
        elif broken:
            status = "⚠️"
        else:
            status = "✅"
        sr_str = f"⭐{m.star_rating:.1f}" if (m.star_rating or 0) > 0 else "⭐<i>—</i>"
        lines.append(
            f"{status} <code>{m.beatmap_id}</code> {escape_html(m.artist)} - {escape_html(m.title)} "
            f"[{escape_html(m.version)}] {sr_str} {m.map_type or ''}"
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
    """Re-derive skill stars and map_type from stored features without re-downloading.

    Uses the new analyze_map pipeline.  For maps with cached parser features
    (f_burst, f_stream, ...) we feed those back in; for maps without features
    we fall back to metadata (BPM/AR/OD/length) only.
    """
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.osu_parser import (
        compute_skill_stars, stars_to_weights, map_type_from_stars,
    )

    wait = await message.answer("Пересчитываю звёзды и веса карт в пуле…")

    updated = 0
    type_counts: dict[str, int] = {}

    async with get_db_session() as session:
        maps = (await session.execute(select(BskMapPool))).scalars().all()
        for m in maps:
            # Reconstruct feature dict from stored columns
            features = {
                "note_count":            m.f_note_count or 0,
                "duration_seconds":      m.f_duration or m.length or 0,
                "rhythm_complexity":     m.f_rhythm_complexity or 0.0,
                "stream_density":        (m.f_burst or 0) + (m.f_stream or 0) + (m.f_death_stream or 0),
                "jump_density":          m.f_jump_density or 0.0,
                "avg_jump_velocity":     m.f_jump_vel or 0.0,
                "back_forth_ratio":      m.f_back_forth or 0.0,
                "angle_variance":        m.f_angle_var or 0.0,
                "flow_break_density":    m.f_flow_break or 0.0,
                "burst_density":         m.f_burst or 0.0,
                "full_stream_density":   m.f_stream or 0.0,
                "death_stream_density":  m.f_death_stream or 0.0,
                "bpm_rel_speed":         m.f_bpm_rel_speed or 0.0,
                "subdiv_entropy":        m.f_subdiv_entropy or 0.0,
                "polyrhythm_density":    m.f_polyrhythm_density or 0.0,
                "off_beat_ratio":        m.f_off_beat_ratio or 0.0,
                "jack_density":          m.f_jack_density or 0.0,
                "slider_tail_demand":    m.f_slider_tail_demand or 0.0,
                "sv_variance":           m.f_sv_var or 0.0,
                "slider_density":        m.f_slider_density or 0.0,
                "density_variance":      m.f_density_var or 0.0,
                "intensity_floor":       m.f_intensity_floor or 0.0,
                "pattern_repetition":    m.f_pattern_repeat or 0.0,
            }
            stars = compute_skill_stars(
                features,
                bpm=m.bpm or 0, ar=m.ar or 0, od=m.od or 0,
                length_s=m.length or 0,
                star_rating=m.star_rating or 0,
                api_aim=float(m.api_aim_diff or 0.0),
                api_speed=float(m.api_speed_diff or 0.0),
            )
            weights = stars_to_weights(stars)

            m.aim_stars   = stars["aim"]
            m.speed_stars = stars["speed"]
            m.acc_stars   = stars["acc"]
            m.cons_stars  = stars["cons"]
            m.w_aim   = weights["aim"]
            m.w_speed = weights["speed"]
            m.w_acc   = weights["acc"]
            m.w_cons  = weights["cons"]
            m.map_type = map_type_from_stars(stars)
            type_counts[m.map_type] = type_counts.get(m.map_type, 0) + 1
            updated += 1
        await session.commit()

    lines = [f"✅ Пересчитано карт: <b>{updated}</b>\n", "<b>Распределение по типам:</b>"]
    for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct = cnt / max(updated, 1) * 100
        lines.append(f"  • <code>{t:<6}</code>  {cnt}  ({pct:.1f}%)")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("bskreanalyze"))
async def cmd_bsk_reanalyze(message: types.Message, osu_api_client):
    """
    Re-download every map's .osu file, extract deep features + osu! API attributes,
    write per-skill stars + map_type via the new analyze_map pipeline.
    Takes a few minutes for large pools.
    """
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.map_pool import analyze_map, apply_to_entry
    import asyncio

    wait = await message.answer("🔍 Глубокий анализ пула карт…\nЭто может занять несколько минут.")

    updated = 0
    failed  = 0
    no_osu  = 0

    async with get_db_session() as session:
        maps = (await session.execute(select(BskMapPool))).scalars().all()
        total = len(maps)

    for idx, m in enumerate(maps, 1):
        if idx % 20 == 0:
            try:
                await wait.edit_text(
                    f"🔍 Анализ: {idx}/{total} карт…\n"
                    f"✅ {updated}  ❌ {failed}  ⏭ {no_osu}"
                )
            except Exception:
                logger.debug("bskreanalyze: progress edit_text failed", exc_info=True)

        # Download .osu
        osu_bytes = None
        try:
            osu_bytes = await osu_api_client.download_osu_file(m.beatmap_id)
        except Exception:
            logger.debug(f"bskreanalyze: .osu download failed for {m.beatmap_id}", exc_info=True)

        # Fetch beatmap data (for hp_drain + chance to repair sr=0 entries).
        hp_drain_val = None
        api_sr = api_bpm = api_length = None
        api_ar = api_od = api_cs = None
        try:
            bmap_data = await osu_api_client.get_beatmap(m.beatmap_id)
            if bmap_data:
                hp_drain_val = float(bmap_data.get("drain") or 0) or None
                api_sr     = float(bmap_data.get("difficulty_rating") or 0) or None
                api_bpm    = float(bmap_data.get("bpm") or 0) or None
                api_length = int(bmap_data.get("total_length") or bmap_data.get("hit_length") or 0) or None
                api_ar     = float(bmap_data.get("ar")       or 0) or None
                api_od     = float(bmap_data.get("accuracy") or 0) or None
                api_cs     = float(bmap_data.get("cs")       or 0) or None
        except Exception:
            logger.debug(f"bskreanalyze: get_beatmap failed for {m.beatmap_id}", exc_info=True)

        # Fetch API attributes (absolute aim/speed difficulties)
        api_aim = api_speed = api_slider = api_speed_notes = None
        try:
            attrs = await osu_api_client.get_beatmap_attributes(m.beatmap_id)
            if attrs:
                api_aim         = attrs.get("aim_difficulty")
                api_speed       = attrs.get("speed_difficulty")
                api_slider      = attrs.get("slider_factor")
                api_speed_notes = attrs.get("speed_note_count")
        except Exception:
            logger.debug(f"bskreanalyze: get_beatmap_attributes failed for {m.beatmap_id}", exc_info=True)

        osu_text = osu_bytes.decode("utf-8", errors="replace") if osu_bytes else None
        if not osu_text:
            no_osu += 1

        try:
            # Prefer fresh API values over stale row data — heals sr=0 entries.
            eff_bpm    = api_bpm    or (m.bpm or 0)
            eff_length = api_length or (m.length or 0)
            eff_sr     = api_sr     or (m.star_rating or 0)
            eff_ar     = api_ar     or (m.ar or 0)
            eff_od     = api_od     or (m.od or 0)
            result = analyze_map(
                osu_text,
                bpm=eff_bpm, ar=eff_ar, od=eff_od,
                length_s=eff_length,
                star_rating=eff_sr,
                api_aim=float(api_aim or 0.0),
                api_speed=float(api_speed or 0.0),
            )
            async with get_db_session() as session:
                entry = (await session.execute(
                    select(BskMapPool).where(BskMapPool.beatmap_id == m.beatmap_id)
                )).scalar_one_or_none()
                if entry:
                    entry.api_aim_diff         = api_aim
                    entry.api_speed_diff       = api_speed
                    entry.api_slider_factor    = api_slider
                    entry.api_speed_note_count = api_speed_notes
                    if hp_drain_val is not None:
                        entry.hp_drain = hp_drain_val
                    # Heal sr=0 / missing-metadata entries when the API now
                    # returns sane values. Don't overwrite with zeros.
                    if api_sr     is not None: entry.star_rating = api_sr
                    if api_bpm    is not None: entry.bpm         = api_bpm
                    if api_length is not None: entry.length      = api_length
                    if api_ar     is not None: entry.ar          = api_ar
                    if api_od     is not None: entry.od          = api_od
                    if api_cs     is not None: entry.cs          = api_cs
                    apply_to_entry(entry, result)
                    await session.commit()
            updated += 1
        except Exception as e:
            logger.warning(f"bskreanalyze: failed for {m.beatmap_id}: {e}")
            failed += 1

        await asyncio.sleep(0.15)  # rate-limit CDN + API calls

    await wait.edit_text(
        f"✅ <b>Глубокий анализ завершён</b>\n\n"
        f"Обновлено:       <b>{updated}</b>\n"
        f"Без .osu файла:  <b>{no_osu}</b>\n"
        f"Ошибок:          <b>{failed}</b>",
        parse_mode="HTML",
    )


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
    _cleanup_stale_imports()
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

    wait = await message.answer("Скачиваю файл в очередь импорта...")
    try:
        tmp_path, size = await _download_url_to_import_file(url, max_bytes=MAX_IMPORT_FILE_SIZE)
    except Exception as e:
        await wait.edit_text(f"Ошибка при скачивании: {escape_html(str(e))}", parse_mode="HTML")
        return

    slot_id = _register_import(message.from_user.id, tmp_path, url.split("/")[-1], size)
    osz_count, osu_count = _count_osu_files(tmp_path)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Импортировать", callback_data=f"bskimport:confirm:{slot_id}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"bskimport:cancel:{slot_id}"),
    ]])
    await wait.edit_text(
        f"<b>Предпросмотр импорта</b>\n\n"
        f"Источник: <code>{escape_html(url.split('/')[-1])}</code>\n"
        f"Размер: <b>{_fmt_bytes(size)}</b>\n"
        f"Архивов .osz: <b>{osz_count}</b>\n"
        f"Карт .osu: <b>{osu_count}</b>\n"
        f"Слот: <b>{_queue_position(slot_id)}/{MAX_IMPORT_SLOTS}</b>\n"
        f"Одновременно выполняется импортов: <b>{MAX_RUNNING_IMPORTS}</b>\n\n"
        f"Подтвердить импорт в BSK пул?",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ─── Import queue ─────────────────────────────────────────────────────────────

MAX_IMPORT_SLOTS = 5
MAX_RUNNING_IMPORTS = 1
MAX_IMPORT_FILE_SIZE = 1024 * 1024 * 1024
IMPORT_TMP_DIR = "/tmp/project1984_bsk_imports"
IMPORT_PENDING_TTL_SECONDS = 60 * 60
# slot_id -> {tg_id, file_path, filename, status, size, created_at}
_import_queue: dict[str, dict] = {}
# Pending previews: admin_tg_id -> file_path (legacy path)
_pending_imports: dict[int, str] = {}
_import_semaphore = None


def _get_import_semaphore():
    global _import_semaphore
    if _import_semaphore is None:
        import asyncio
        _import_semaphore = asyncio.Semaphore(MAX_RUNNING_IMPORTS)
    return _import_semaphore


def _fmt_bytes(n: int | None) -> str:
    n = int(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def _cleanup_import_file(path: str | None) -> None:
    if not path:
        return
    try:
        import os
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("bskimport: temp cleanup failed", exc_info=True)


def _cleanup_stale_imports() -> None:
    """Remove expired pending import previews and orphan temp files."""
    import os
    now = datetime.utcnow()
    expired: list[str] = []
    for slot_id, slot in list(_import_queue.items()):
        if slot.get("status") not in ("pending", "queued"):
            continue
        created_at = slot.get("created_at")
        if not isinstance(created_at, datetime):
            continue
        if (now - created_at).total_seconds() > IMPORT_PENDING_TTL_SECONDS:
            expired.append(slot_id)

    for slot_id in expired:
        slot = _import_queue.pop(slot_id, None)
        if slot:
            _cleanup_import_file(slot.get("file_path"))

    try:
        os.makedirs(IMPORT_TMP_DIR, exist_ok=True)
        cutoff = now.timestamp() - IMPORT_PENDING_TTL_SECONDS
        active_paths = {slot.get("file_path") for slot in _import_queue.values()}
        for name in os.listdir(IMPORT_TMP_DIR):
            path = os.path.join(IMPORT_TMP_DIR, name)
            if path in active_paths or not name.startswith("bskimport_"):
                continue
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except FileNotFoundError:
                pass
    except Exception:
        logger.debug("bskimport: stale temp cleanup failed", exc_info=True)


def _register_import(tg_id: int, file_path: str, filename: str, size: int = 0) -> str:
    import uuid
    slot_id = str(uuid.uuid4())[:8]
    _import_queue[slot_id] = {
        "tg_id": tg_id,
        "file_path": file_path,
        "filename": filename,
        "status": "pending",
        "size": int(size or 0),
        "created_at": datetime.utcnow(),
    }
    return slot_id


def _queue_position(slot_id: str) -> int:
    return list(_import_queue.keys()).index(slot_id) + 1 if slot_id in _import_queue else 0


async def _validate_public_import_url(url: str) -> str:
    """Validate import URL and reject localhost/private-network targets."""
    import asyncio
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("Разрешены только http/https ссылки.")
    if not parsed.hostname:
        raise RuntimeError("Некорректная ссылка: нет hostname.")
    if parsed.username or parsed.password:
        raise RuntimeError("Ссылки с username/password не поддерживаются.")

    host = parsed.hostname.strip().rstrip(".")
    if not host:
        raise RuntimeError("Некорректная ссылка: пустой hostname.")

    def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal and _is_blocked_ip(literal):
        raise RuntimeError("Ссылка ведёт на запрещённый адрес.")

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise RuntimeError("Не удалось разрешить hostname ссылки.")

    resolved_ips = set()
    for _family, _type, _proto, _canon, sockaddr in infos:
        if not sockaddr:
            continue
        ip_raw = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_raw)
        except ValueError:
            raise RuntimeError("Hostname ссылки разрешился в некорректный адрес.")
        resolved_ips.add(str(ip))
        if _is_blocked_ip(ip):
            raise RuntimeError("Ссылка ведёт во внутреннюю/локальную сеть.")

    if not resolved_ips:
        raise RuntimeError("Hostname ссылки не вернул IP-адресов.")
    return url


async def _download_url_to_import_file(url: str, max_bytes: int = MAX_IMPORT_FILE_SIZE) -> tuple[str, int]:
    import aiohttp as _aiohttp
    import os
    import tempfile
    from urllib.parse import urljoin

    os.makedirs(IMPORT_TMP_DIR, exist_ok=True)
    suffix = ".osz" if url.lower().split("?", 1)[0].endswith(".osz") else ".zip"
    fd, tmp_path = tempfile.mkstemp(prefix="bskimport_", suffix=suffix, dir=IMPORT_TMP_DIR)
    size = 0
    current_url = url
    redirects_left = 5
    try:
        with os.fdopen(fd, "wb") as f:
            async with _aiohttp.ClientSession() as sess:
                while True:
                    current_url = await _validate_public_import_url(current_url)
                    async with sess.get(
                        current_url,
                        timeout=_aiohttp.ClientTimeout(total=600),
                        allow_redirects=False,
                    ) as resp:
                        if resp.status in (301, 302, 303, 307, 308):
                            if redirects_left <= 0:
                                raise RuntimeError("Слишком много редиректов при скачивании.")
                            location = resp.headers.get("Location")
                            if not location:
                                raise RuntimeError("Редирект без Location.")
                            current_url = urljoin(current_url, location)
                            redirects_left -= 1
                            continue

                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}")
                        if resp.content_length and resp.content_length > max_bytes:
                            raise RuntimeError("Файл слишком большой (макс. 1 GB).")

                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            if not chunk:
                                continue
                            size += len(chunk)
                            if size > max_bytes:
                                raise RuntimeError("Файл слишком большой (макс. 1 GB).")
                            f.write(chunk)
                        break
        return tmp_path, size
    except Exception:
        _cleanup_import_file(tmp_path)
        raise


def _count_osu_files(file_path: str) -> tuple[int, int]:
    import zipfile as _zf
    osz_count = osu_count = 0
    try:
        with _zf.ZipFile(file_path) as outer:
            for name in outer.namelist():
                if name.lower().endswith(".osz"):
                    osz_count += 1
                    try:
                        import io as _io
                        with _zf.ZipFile(_io.BytesIO(outer.read(name))) as inner:
                            osu_count += sum(1 for n in inner.namelist() if n.endswith(".osu"))
                    except Exception:
                        logger.debug(f"bskimport: nested zip read failed for {name}", exc_info=True)
                elif name.lower().endswith(".osu"):
                    osu_count += 1
    except _zf.BadZipFile:
        try:
            with _zf.ZipFile(file_path) as inner:
                osz_count = 1
                osu_count = sum(1 for n in inner.namelist() if n.endswith(".osu"))
        except Exception:
            logger.debug("bskimport: zip recovery read failed", exc_info=True)
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
            path = _pending_imports.pop(tg_id, None)
            _cleanup_import_file(path)
            await callback.message.edit_text("Импорт отменён.")
            await callback.answer()
            return
        file_path = _pending_imports.pop(tg_id, None)
        if not file_path:
            await callback.answer("Сессия истекла. Загрузите файл заново.", show_alert=True)
            return
        slot_id = _register_import(tg_id, file_path, "upload.zip")

    slot = _import_queue.get(slot_id)
    if not slot:
        await callback.answer("Сессия истекла. Загрузите файл заново.", show_alert=True)
        return

    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не ваш импорт.", show_alert=True)
        return

    if action == "cancel":
        _import_queue.pop(slot_id, None)
        _cleanup_import_file(slot.get("file_path"))
        await callback.message.edit_text("Импорт отменён.")
        await callback.answer()
        return

    # Confirm — enqueue import in background; semaphore prevents parallel heavy imports.
    slot["status"] = "queued"
    await callback.message.edit_text(
        f"<b>Импорт поставлен в очередь</b>\n"
        f"Файл: <b>{escape_html(slot['filename'])}</b>\n"
        f"Размер: <b>{_fmt_bytes(slot.get('size'))}</b>\n"
        f"Параллельных импортов: <b>{MAX_RUNNING_IMPORTS}</b>",
        parse_mode="HTML",
    )
    await callback.answer()

    import asyncio
    msg = callback.message

    async def _run():
        result = None
        try:
            async with _get_import_semaphore():
                if slot_id not in _import_queue:
                    return
                slot["status"] = "running"
                try:
                    await msg.edit_text(
                        f"<b>Импортирую карты...</b>\n"
                        f"Файл: <b>{escape_html(slot['filename'])}</b>\n"
                        f"Остальные импорты ждут в очереди.",
                        parse_mode="HTML",
                    )
                except Exception:
                    logger.debug("bskimport: running edit_text failed", exc_info=True)

                from services.bsk.bulk_import import import_from_file
                result = await import_from_file(slot["file_path"], osu_api_client)
        except Exception as e:
            logger.error(f"BSK bulk import error: {e}", exc_info=True)
            result = {"added": 0, "skipped": 0, "failed": 1, "errors": [str(e)]}
        finally:
            _import_queue.pop(slot_id, None)
            _cleanup_import_file(slot.get("file_path"))

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
            logger.debug("bskimport: result edit_text failed", exc_info=True)

    asyncio.create_task(_run())


@router.message(F.document & (F.caption.lower() == "bskimport"))
async def cmd_bsk_bulk_import(message: types.Message, osu_api_client):
    _cleanup_stale_imports()
    doc = message.document
    fname = (doc.file_name or "").lower()
    if not (fname.endswith(".zip") or fname.endswith(".osz")):
        await message.answer("Поддерживаются только файлы <b>.zip</b> или <b>.osz</b>.", parse_mode="HTML")
        return

    if len(_import_queue) >= MAX_IMPORT_SLOTS:
        await message.answer(f"Очередь импорта заполнена (макс. {MAX_IMPORT_SLOTS}). Подождите завершения текущих.")
        return

    if doc.file_size and doc.file_size > MAX_IMPORT_FILE_SIZE:
        await message.answer("Файл слишком большой (макс. 1 GB).")
        return

    wait = await message.answer("Скачиваю файл в очередь импорта...")
    try:
        from config.settings import TELEGRAM_BOT_TOKEN
        file = await message.bot.get_file(doc.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file.file_path}"
        tmp_path, size = await _download_url_to_import_file(file_url, max_bytes=MAX_IMPORT_FILE_SIZE)
    except Exception as e:
        await wait.edit_text(f"Не удалось скачать файл: {escape_html(str(e))}", parse_mode="HTML")
        return

    slot_id = _register_import(message.from_user.id, tmp_path, doc.file_name or "upload.zip", size)
    osz_count, osu_count = _count_osu_files(tmp_path)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Импортировать", callback_data=f"bskimport:confirm:{slot_id}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"bskimport:cancel:{slot_id}"),
    ]])
    await wait.edit_text(
        f"<b>Предпросмотр импорта</b>\n\n"
        f"Файл: <b>{escape_html(doc.file_name)}</b>\n"
        f"Размер: <b>{_fmt_bytes(size)}</b>\n"
        f"Архивов .osz: <b>{osz_count}</b>\n"
        f"Карт .osu: <b>{osu_count}</b>\n"
        f"Слот: <b>{_queue_position(slot_id)}/{MAX_IMPORT_SLOTS}</b>\n"
        f"Одновременно выполняется импортов: <b>{MAX_RUNNING_IMPORTS}</b>\n\n"
        f"Подтвердить импорт в BSK пул?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.message(TextTriggerFilter("bskimportqueue", "bskiq"))
async def cmd_bsk_import_queue(message: types.Message):
    _cleanup_stale_imports()
    if not _import_queue:
        await message.answer("Очередь импорта пуста.")
        return
    lines = ["<b>Очередь импорта BSK</b>\n"]
    for i, (_sid, slot) in enumerate(_import_queue.items(), 1):
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
        # Test duels stay in the topic where the admin invoked them — they
        # ignore BSK_DUEL_THREAD_ID so they don't pollute the public duel feed.
        thread_id=getattr(message, "message_thread_id", None),
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

        from db.models.bsk_duel import BskDuel as _BskDuel
        duel = (await session.execute(
            select(_BskDuel).where(
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

        from db.models.bsk_duel import BskDuel as _BskDuel
        duel = (await session.execute(
            select(_BskDuel).where(
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
    from tasks.bsk_ml_trainer import is_running

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
        from tasks.bsk_ml_trainer import run_nightly_training
        result = await run_nightly_training(triggered_by=f"admin:{message.from_user.id}")
        status = result.get("status", "?")
        if status == "skipped":
            text = f"Недостаточно данных.\nРаундов: <b>{result.get('rounds_used', 0)}</b> (нужно ≥50)"
        elif status == "ok":
            rf_trained = bool(result.get("global_model_trained"))
            rf_samples = result.get("global_model_samples", 0)
            oob = result.get("oob_r2")
            if rf_trained:
                oob_str = f", OOB R²={oob:.3f}" if oob is not None else ""
                rf_line = f"🌲 Глобальный RF: <b>обучен</b> ({rf_samples} карт{oob_str})"
            else:
                rf_line = f"🌲 Глобальный RF: <b>не обучен</b> (мало карт с данными: {rf_samples})"

            # Top-3 features by importance, if model produced them.
            top_str = ""
            fi_json = result.get("feature_importances")
            if fi_json:
                try:
                    import json as _json
                    fi = _json.loads(fi_json)
                    top = fi.get("top", [])[:3]
                    if top:
                        top_str = "\n📊 Top фичи: " + ", ".join(
                            f"<code>{t['name']}</code> ({t['imp']:.2f})" for t in top
                        )
                except Exception:
                    logger.debug("bsktrainml: feature_importances JSON parse failed", exc_info=True)

            text = (
                f"<b>ML обучение завершено</b>\n\n"
                f"Раундов: <b>{result.get('rounds_used', 0)}</b>\n"
                f"{rf_line}{top_str}\n\n"
                f"💪 От данных: <b>{result.get('maps_data_driven', 0)}</b>\n"
                f"🌲 От RF-приора: <b>{result.get('maps_rf_prior', 0)}</b>\n"
                f"📐 От эвристики: <b>{result.get('maps_heuristic', 0)}</b>\n"
                f"⏭ Пропущено (мало раундов на карту): <b>{result.get('maps_skipped', 0)}</b>"
            )
        elif status == "cancelled":
            text = f"<b>Обучение отменено.</b>\nКарт обновлено до отмены: <b>{result.get('maps_updated', 0)}</b>"
        elif status == "timeout":
            text = f"Обучение прервано по таймауту (3 часа).\nОбновлено: <b>{result.get('maps_updated', 0)}</b>"
        else:
            text = f"Ошибка: {result.get('error', '?')}"
        try:
            await wait.edit_text(text, parse_mode="HTML", reply_markup=_ml_monitor_keyboard(False, False))
        except Exception:
            logger.debug("bsktrainml: result edit_text failed", exc_info=True)

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
                logger.debug("bskml control: result edit_text failed", exc_info=True)
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

    async with get_db_session() as session:
        runs = (await session.execute(
            select(BskMlRun).order_by(desc(BskMlRun.ran_at)).limit(5)
        )).scalars().all()

        total_rounds = (await session.execute(
            select(func.count()).select_from(BskDuelRound).where(
                BskDuelRound.status == "completed",
                BskDuelRound.player1_composite.isnot(None),
            )
        )).scalar() or 0

    # Next scheduled run (in configured local timezone — must match scheduler)
    from zoneinfo import ZoneInfo
    from config.settings import TIMEZONE
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
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
                # New honest breakdown — fall back to legacy single counter for old rows.
                if r.maps_data_driven is not None:
                    if r.global_model_trained:
                        oob = getattr(r, "oob_r2", None)
                        oob_str = f", OOB R²={oob:.2f}" if oob is not None else ""
                        rf_state = f"🌲 RF✓ ({r.global_model_samples} карт{oob_str})"
                    else:
                        rf_state = "🌲 RF✗"
                    breakdown = (
                        f"💪 {r.maps_data_driven} от данных · "
                        f"🌲 {r.maps_rf_prior or 0} от RF · "
                        f"📐 {r.maps_heuristic or 0} от эвристики"
                    )
                    lines.append(
                        f"✅ {ts} [{trigger}] · {r.rounds_used} раундов · {rf_state}{acc_str}\n"
                        f"   {breakdown}"
                    )
                else:
                    lines.append(
                        f"✅ {ts} [{trigger}] — обновлено {r.maps_updated} карт "
                        f"из {r.rounds_used} раундов{acc_str}"
                    )
            elif r.status == "skipped":
                lines.append(f"⏭ {ts} [{trigger}] — мало данных ({r.rounds_used} раундов)")
            elif r.status == "timeout":
                lines.append(f"⏰ {ts} [{trigger}] — таймаут{acc_str}")
            else:
                lines.append(f"❌ {ts} [{trigger}] — ошибка: {r.notes or '?'}")
    else:
        lines.append("Запусков ещё не было.")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ─── BSK pool diagnostic dump  (Phase 1 of skill metric overhaul) ────────────

def _percentiles(values: list[float], pcts: list[float]) -> list[float]:
    """Return values at requested percentiles (0..1) from a sample."""
    if not values:
        return [0.0] * len(pcts)
    s = sorted(values)
    out = []
    for p in pcts:
        idx = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
        out.append(s[idx])
    return out


def _fmt_pct(values: list[float]) -> str:
    """Format a sample as `min / p25 / p50 / p75 / max  (mean ± std)`."""
    if not values:
        return "—"
    import math
    p = _percentiles(values, [0.0, 0.25, 0.50, 0.75, 1.0])
    mean = sum(values) / len(values)
    std  = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    return (f"{p[0]:.3f} / {p[1]:.3f} / <b>{p[2]:.3f}</b> / {p[3]:.3f} / {p[4]:.3f}"
            f"   μ={mean:.3f} σ={std:.3f}")


# ─── BSK rating reset (admin-only, double-confirm) ───────────────────────────
# Hard-resets every player's BSK rating components. There used to be a
# migration `bsk_reset_calibration` that ran on every bot start and silently
# wiped progress; it was removed. This explicit command replaces it as the
# *only* way to do a global reset, and it requires a confirmation tap.

# slot_id -> {tg_id: int, mode: str, seed: str, created_at: datetime}
_bskreset_slots: dict[str, dict] = {}


def _register_bskreset_slot(tg_id: int, mode: str, seed: str) -> str:
    slot_id = uuid4().hex[:8]
    _bskreset_slots[slot_id] = {
        "tg_id": tg_id,
        "mode": mode,
        "seed": seed,
        "created_at": datetime.utcnow(),
    }
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    for sid, data in list(_bskreset_slots.items()):
        if data.get("created_at") and data["created_at"] < cutoff:
            _bskreset_slots.pop(sid, None)
    return slot_id


@router.message(TextTriggerFilter("bskreset"))
async def cmd_bsk_reset(message: types.Message, trigger_args: TriggerArgs):
    """bskreset [casual|ranked|all] [pp|flat] — reset every player's BSK rating.

    Modes (default `all`):
      - <code>casual</code>  — only casual ratings
      - <code>ranked</code>  — only ranked ratings
      - <code>all</code>     — both modes

    Seed (default `pp`):
      - <code>pp</code>    — re-seed each player from their current osu! pp
                            via <code>starting_mu_from_pp()</code>
      - <code>flat</code>  — hard reset to 250/250/250/250 (raw model defaults)

    Both wins/losses, sigma, peak_mu and placement_matches_left are reset too.
    Requires a confirmation tap; nothing is written until you press the button.
    """
    from db.models.bsk_rating import BskRating
    from sqlalchemy import func as _f

    raw = (trigger_args.args or "").strip().lower().split()
    mode = "all"
    seed = "pp"
    for tok in raw:
        if tok in ("casual", "ranked", "all"):
            mode = tok
        elif tok in ("pp", "flat"):
            seed = tok

    # Count what would be affected so the admin sees the blast radius.
    async with get_db_session() as session:
        if mode == "all":
            total = (await session.execute(
                select(_f.count()).select_from(BskRating)
            )).scalar() or 0
        else:
            total = (await session.execute(
                select(_f.count()).select_from(BskRating).where(BskRating.mode == mode)
            )).scalar() or 0

    if total == 0:
        await message.answer("Нечего сбрасывать — таблица BSK-рейтингов пуста.")
        return

    seed_label = (
        "по pp игроков (через <code>starting_mu_from_pp</code>)"
        if seed == "pp" else "плоский (250/250/250/250)"
    )
    mode_label = {"all": "обоих режимов (casual + ranked)",
                  "casual": "casual", "ranked": "ranked"}[mode]

    slot = _register_bskreset_slot(message.from_user.id, mode, seed)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=f"⚠️ Сбросить {total}",
                callback_data=f"bskreset:apply:{slot}",
            ),
            types.InlineKeyboardButton(
                text="Отмена",
                callback_data=f"bskreset:cancel:{slot}",
            ),
        ],
    ])

    await message.answer(
        "<b>Сброс рейтингов BSK</b>\n\n"
        f"Будет сброшено: <b>{total}</b> рейтинг(ов).\n"
        f"Режим: <b>{mode_label}</b>\n"
        f"Seed: {seed_label}\n\n"
        "Это <b>необратимо</b>. Будут затёрты:\n"
        " • <code>mu_aim / mu_speed / mu_acc / mu_cons</code>\n"
        " • <code>sigma_*</code> → 100\n"
        " • <code>placement_matches_left</code> → 10\n"
        " • <code>wins / losses</code> → 0\n"
        " • <code>peak_mu</code> → стартовое значение\n\n"
        "История дуэлей в <code>bsk_duels</code> и раунды останутся нетронутыми.\n"
        "Подтвердить?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("bskreset:"))
async def on_bsk_reset_callback(callback: types.CallbackQuery):
    """Confirm/cancel for `bskreset`. Performs the destructive UPDATE."""
    from db.models.bsk_rating import BskRating
    from services.bsk.rating import starting_mu_from_pp

    parts = callback.data.split(":")
    # bskreset:<action>:<slot>
    if len(parts) != 3:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    action = parts[1]
    slot_id = parts[2]

    slot = _bskreset_slots.get(slot_id)
    if not slot:
        await callback.answer("Сессия истекла.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("bskreset: edit_reply_markup failed (expired slot)", exc_info=True)
        return

    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не твой запрос.", show_alert=True)
        return

    if action == "cancel":
        _bskreset_slots.pop(slot_id, None)
        try:
            await callback.message.edit_text(
                (callback.message.html_text or "") + "\n\n<b>Отменено.</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            logger.debug("bskreset: cancel edit_text failed", exc_info=True)
        await callback.answer("Отменено.")
        return

    if action != "apply":
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    mode: str = slot["mode"]
    seed: str = slot["seed"]
    _bskreset_slots.pop(slot_id, None)

    # ── Apply ───────────────────────────────────────────────────────────────
    affected = 0
    async with get_db_session() as session:
        stmt = select(BskRating)
        if mode != "all":
            stmt = stmt.where(BskRating.mode == mode)
        ratings = (await session.execute(stmt)).scalars().all()

        # Pre-fetch player_pp for the pp-seed mode in a single query to avoid
        # N round-trips when the pool is large.
        pp_by_user: dict[int, float] = {}
        if seed == "pp" and ratings:
            user_ids = list({r.user_id for r in ratings})
            from db.models.user import User
            urows = (await session.execute(
                select(User.id, User.player_pp).where(User.id.in_(user_ids))
            )).all()
            pp_by_user = {uid: float(pp or 0.0) for uid, pp in urows}

        for r in ratings:
            if seed == "pp":
                start_mu = starting_mu_from_pp(pp_by_user.get(r.user_id, 0.0))
            else:  # flat
                start_mu = 1000.0
            per_comp = start_mu / 4.0

            r.mu_aim   = per_comp
            r.mu_speed = per_comp
            r.mu_acc   = per_comp
            r.mu_cons  = per_comp
            r.sigma_aim   = 100.0
            r.sigma_speed = 100.0
            r.sigma_acc   = 100.0
            r.sigma_cons  = 100.0
            r.placement_matches_left = 10
            r.wins = 0
            r.losses = 0
            r.peak_mu = start_mu
            r.updated_at = datetime.utcnow()
            affected += 1

        await session.commit()

    logger.warning(
        f"bskreset applied by admin tg_id={callback.from_user.id} "
        f"mode={mode} seed={seed} affected={affected}"
    )

    try:
        new_text = (
            (callback.message.html_text or "")
            + f"\n\n<b>✅ Сброшено: {affected}</b>"
            + f"\nseed=<code>{seed}</code>, mode=<code>{mode}</code>"
        )
        await callback.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("bskreset: post-apply edit_reply_markup failed", exc_info=True)
        await callback.message.answer(
            f"✅ Сброшено: <b>{affected}</b> рейтинг(ов).",
            parse_mode="HTML",
        )

    await callback.answer(f"Сброшено: {affected}")


@router.message(TextTriggerFilter("bskdiag"))
async def cmd_bsk_diag(message: types.Message):
    """bskdiag — diagnostic snapshot of the BSK map pool (post-Phase-2).

    Shows distribution of map_type by stars, percentile ranges of *_stars,
    average parser features per type, and top picks per skill.
    Read-only, no DB writes.
    """
    from db.models.bsk_map_pool import BskMapPool

    wait = await message.answer("Считаю диагностику пула…")

    async with get_db_session() as session:
        maps = (await session.execute(
            select(BskMapPool).where(BskMapPool.enabled == True)  # noqa: E712
        )).scalars().all()

    if not maps:
        await wait.edit_text("Пул пуст.", parse_mode="HTML")
        return

    n = len(maps)

    # ── 1. map_type distribution ──────────────────────────────────────────
    type_counts: dict[str, int] = {}
    for m in maps:
        t = m.map_type or "—"
        type_counts[t] = type_counts.get(t, 0) + 1

    # ── 2. star + weight percentiles per skill axis ───────────────────────
    star_buckets = {
        "aim":   [m.aim_stars   for m in maps if m.aim_stars   is not None],
        "speed": [m.speed_stars for m in maps if m.speed_stars is not None],
        "acc":   [m.acc_stars   for m in maps if m.acc_stars   is not None],
        "cons":  [m.cons_stars  for m in maps if m.cons_stars  is not None],
    }
    w_buckets = {
        "aim":   [m.w_aim   or 0.0 for m in maps],
        "speed": [m.w_speed or 0.0 for m in maps],
        "acc":   [m.w_acc   or 0.0 for m in maps],
        "cons":  [m.w_cons  or 0.0 for m in maps],
    }

    # ── 3. argmax sanity check (in case map_type lags stars) ──────────────
    argmax_counts = {"aim": 0, "speed": 0, "acc": 0, "cons": 0}
    has_stars = 0
    for m in maps:
        if m.aim_stars is None and m.speed_stars is None and m.acc_stars is None and m.cons_stars is None:
            continue
        has_stars += 1
        ss = {"aim": m.aim_stars or 0, "speed": m.speed_stars or 0,
              "acc": m.acc_stars or 0, "cons": m.cons_stars or 0}
        argmax_counts[max(ss, key=ss.get)] += 1

    # ── 4. parser feature averages by current map_type ────────────────────
    feat_keys = [
        ("subdiv_ent", "f_subdiv_entropy"),
        ("polyrhy",    "f_polyrhythm_density"),
        ("off_beat",   "f_off_beat_ratio"),
        ("jack",       "f_jack_density"),
        ("od_dem",     "f_od_demand"),
        ("flow_brk",   "f_flow_break"),
        ("jump_dens",  "f_jump_density"),
        ("jump_vel",   "f_jump_vel"),
        ("bpm_rel",    "f_bpm_rel_speed"),
        ("stream",     "f_stream"),
        ("burst",      "f_burst"),
        ("density_v",  "f_density_var"),
        ("int_floor",  "f_intensity_floor"),
        ("repeat",     "f_pattern_repeat"),
    ]
    feat_by_type: dict[str, dict[str, list[float]]] = {}
    for m in maps:
        t = m.map_type or "—"
        d = feat_by_type.setdefault(t, {})
        for label, attr in feat_keys:
            v = getattr(m, attr, None)
            if v is None:
                continue
            d.setdefault(label, []).append(float(v))

    # ── 5. Top-5 per skill by stars ───────────────────────────────────────
    def _top5(attr: str) -> list:
        vals = [m for m in maps if getattr(m, attr, None) is not None]
        return sorted(vals, key=lambda x: getattr(x, attr) or 0.0, reverse=True)[:5]
    top_aim   = _top5("aim_stars")
    top_speed = _top5("speed_stars")
    top_acc   = _top5("acc_stars")
    top_cons  = _top5("cons_stars")

    # ── 6. SR-band distribution × type ────────────────────────────────────
    sr_bands = [(0, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 12)]
    sr_band_counts: dict[tuple, dict[str, int]] = {b: {} for b in sr_bands}
    for m in maps:
        sr = m.star_rating or 0.0
        for lo, hi in sr_bands:
            if lo <= sr < hi:
                bucket = sr_band_counts[(lo, hi)]
                t = m.map_type or "—"
                bucket[t] = bucket.get(t, 0) + 1
                break

    # ── Build output ──────────────────────────────────────────────────────
    lines = [
        f"<b>BSK pool diagnostic</b>  ·  всего: <b>{n}</b> карт"
        f"  ·  со звёздами: <b>{has_stars}</b>",
        "",
        "<b>① map_type:</b>",
    ]
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct = c / n * 100
        lines.append(f"  • <code>{t:<6}</code>  {c:>5}  ({pct:5.1f}%)")
    lines.append("")

    if has_stars:
        lines.append("<b>② argmax(*_stars) (sanity):</b>")
        for t in ("aim", "speed", "acc", "cons"):
            c = argmax_counts[t]
            pct = c / max(has_stars, 1) * 100
            lines.append(f"  • <code>{t:<6}</code>  {c:>5}  ({pct:5.1f}%)")
        lines.append("")

    # Star percentiles
    if has_stars:
        lines.append("<b>③ Перцентили *_stars [0..10]</b>:")
        for k in ("aim", "speed", "acc", "cons"):
            if star_buckets[k]:
                lines.append(f"  <code>{k:<6}</code>  {_fmt_pct(star_buckets[k])}")
        lines.append("")

    # Weight percentiles
    lines.append("<b>④ Перцентили w_* [0..1]</b>:")
    for k in ("aim", "speed", "acc", "cons"):
        lines.append(f"  <code>{k:<6}</code>  {_fmt_pct(w_buckets[k])}")
    lines.append("")

    # Feature averages per type
    lines.append("<b>⑤ Средние фичи по типам</b>:")
    for t in ("aim", "speed", "acc", "cons", "—"):
        if t not in feat_by_type:
            continue
        lines.append(f"  <i>{t}</i>  ({type_counts.get(t, 0)} карт):")
        d = feat_by_type[t]
        items = [(label, sum(d[label])/len(d[label]) if d.get(label) else None)
                 for label, _ in feat_keys]
        row = []
        for label, val in items:
            row.append(f"{label}={val:.3f}" if val is not None else f"{label}=—")
            if len(row) == 4:
                lines.append("    " + "  ".join(row))
                row = []
        if row:
            lines.append("    " + "  ".join(row))
    lines.append("")

    # SR band breakdown
    lines.append("<b>⑥ Типы по SR-полосам</b>:")
    for (lo, hi) in sr_bands:
        bucket = sr_band_counts[(lo, hi)]
        total_b = sum(bucket.values())
        if total_b == 0:
            continue
        parts = ", ".join(f"{t}:{c}" for t, c in sorted(bucket.items(), key=lambda x: -x[1]))
        lines.append(f"  <code>{lo}–{hi}★</code>  ({total_b}):  {parts}")
    lines.append("")

    # Top maps per skill
    def _fmt_top(label: str, attr: str, top: list) -> list:
        out = [f"<b>{label}:</b>"]
        for i, m in enumerate(top, 1):
            title = (m.title or "?")[:28]
            ver   = (m.version or "")[:18]
            v     = getattr(m, attr) or 0.0
            sr    = m.star_rating or 0.0
            out.append(
                f"  {i}. <code>{v:5.2f}</code>  "
                f"{escape_html(title)} [{escape_html(ver)}]  {sr:.2f}★"
            )
        return out

    lines.append("<b>⑦ Топ карт по каждой шкале</b>")
    lines.extend(_fmt_top("AIM", "aim_stars", top_aim))
    lines.extend(_fmt_top("SPEED", "speed_stars", top_speed))
    lines.extend(_fmt_top("ACC", "acc_stars", top_acc))
    lines.extend(_fmt_top("CONS", "cons_stars", top_cons))

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > 3900 and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(line)
        current_len += len(line) + 1
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    if chunks:
        await wait.edit_text(chunks[0], parse_mode="HTML")
        for chunk in chunks[1:]:
            await message.answer(chunk, parse_mode="HTML")


from bot.handlers.admin.review import (  # noqa: F401  pylint: disable=unused-import
    review_command,
    reviewselect_command,
    review_action,
)


