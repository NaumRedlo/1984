from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto,
)
from sqlalchemy import select

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
