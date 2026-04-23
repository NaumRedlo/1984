from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto,
)
from sqlalchemy import select, func

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.bsk_rating import BskRating
from db.models.user import User
from services.image import card_renderer
from utils.logger import get_logger
from utils.osu.resolve_user import get_any_user_by_telegram_id

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


async def _get_bsk_rank(user_id: int, mode: str, mu_global: float) -> int | None:
    async with get_db_session() as session:
        stmt = select(func.count()).select_from(BskRating).where(
            BskRating.mode == mode,
        )
        total = (await session.execute(stmt)).scalar_one()
        if total == 0:
            return None
        better_stmt = select(func.count()).select_from(BskRating).where(
            BskRating.mode == mode,
            BskRating.user_id != user_id,
        )
        # Count users with higher mu_global — approximate via subquery
        all_stmt = select(BskRating).where(BskRating.mode == mode)
        all_ratings = (await session.execute(all_stmt)).scalars().all()
        rank = 1 + sum(1 for r in all_ratings if r.user_id != user_id and r.mu_global > mu_global)
        return rank


async def _get_bsk_data(user: User, mode: str) -> dict:
    async with get_db_session() as session:
        stmt = select(BskRating).where(
            BskRating.user_id == user.id,
            BskRating.mode == mode,
        )
        rating = (await session.execute(stmt)).scalar_one_or_none()

    if not rating:
        return {
            "username": user.osu_username,
            "country": user.country or "",
            "avatar_url": user.avatar_url,
            "cover_data": user.cover_data,
            "mode": mode,
            "mu_global": 1000.0,
            "mu_aim": 250.0,
            "mu_speed": 250.0,
            "mu_acc": 250.0,
            "mu_cons": 250.0,
            "conservative": 0.0,
            "peak_mu": 1000.0,
            "wins": 0,
            "losses": 0,
            "placement_matches_left": 10,
            "bsk_rank": None,
        }

    bsk_rank = await _get_bsk_rank(user.id, mode, rating.mu_global)

    return {
        "username": user.osu_username,
        "country": user.country or "",
        "avatar_url": user.avatar_url,
        "cover_data": user.cover_data,
        "mode": mode,
        "mu_global": rating.mu_global,
        "mu_aim": rating.mu_aim,
        "mu_speed": rating.mu_speed,
        "mu_acc": rating.mu_acc,
        "mu_cons": rating.mu_cons,
        "conservative": rating.conservative,
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

    mode = "casual"
    data = await _get_bsk_data(user, mode)
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

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, owner_tg_id)

    if not user:
        await callback.answer()
        return

    data = await _get_bsk_data(user, mode)
    img_buf = await card_renderer.generate_bsk_card_async(data)

    await callback.message.edit_media(
        InputMediaPhoto(media=BufferedInputFile(img_buf.read(), filename="bsk.png")),
        reply_markup=_build_bsk_keyboard(owner_tg_id, mode),
    )
    await callback.answer()


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


async def _get_bsk_data(user: User, mode: str) -> dict:
    async with get_db_session() as session:
        stmt = select(BskRating).where(
            BskRating.user_id == user.id,
            BskRating.mode == mode,
        )
        rating = (await session.execute(stmt)).scalar_one_or_none()

    if not rating:
        return {
            "username": user.osu_username,
            "country": user.country or "",
            "avatar_url": user.avatar_url,
            "mode": mode,
            "mu_global": 1000.0,
            "mu_aim": 250.0,
            "mu_speed": 250.0,
            "mu_acc": 250.0,
            "mu_cons": 250.0,
            "conservative": 0.0,
            "peak_mu": 1000.0,
            "wins": 0,
            "losses": 0,
            "placement_matches_left": 10,
        }

    return {
        "username": user.osu_username,
        "country": user.country or "",
        "avatar_url": user.avatar_url,
        "mode": mode,
        "mu_global": rating.mu_global,
        "mu_aim": rating.mu_aim,
        "mu_speed": rating.mu_speed,
        "mu_acc": rating.mu_acc,
        "mu_cons": rating.mu_cons,
        "conservative": rating.conservative,
        "peak_mu": rating.peak_mu,
        "wins": rating.wins,
        "losses": rating.losses,
        "placement_matches_left": rating.placement_matches_left,
    }


@router.message(TextTriggerFilter("bsk"))
async def bsk_profile(message: Message, trigger_args: TriggerArgs):
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)

    if not user or not user.osu_user_id:
        await message.answer("Сначала зарегистрируйтесь: <code>register &lt;nickname&gt;</code>", parse_mode="HTML")
        return

    mode = "casual"
    data = await _get_bsk_data(user, mode)
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

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, owner_tg_id)

    if not user:
        await callback.answer()
        return

    data = await _get_bsk_data(user, mode)
    img_buf = await card_renderer.generate_bsk_card_async(data)

    await callback.message.edit_media(
        InputMediaPhoto(media=BufferedInputFile(img_buf.read(), filename="bsk.png")),
        reply_markup=_build_bsk_keyboard(owner_tg_id, mode),
    )
    await callback.answer()
