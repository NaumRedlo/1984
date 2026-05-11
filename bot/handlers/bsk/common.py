from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, func as sqlfunc

from config.settings import BSK_DUEL_THREAD_ID
from db.database import get_db_session
from db.models.bsk_rating import BskRating
from services.bsk import duel_manager as dm  # noqa: F401 — re-exported for handlers
from utils.osu.resolve_user import get_any_user_by_telegram_id

_looking_for_duel: dict[int, tuple[str, datetime]] = {}
LOOKING_TIMEOUT = timedelta(minutes=15)
ONLINE_THRESHOLD = timedelta(minutes=30)


def resolve_duel_thread(message_or_callback) -> Optional[int]:
    """Return the message_thread_id where a real (non-test) duel should post.

    Priority:
      1. BSK_DUEL_THREAD_ID (env) — if set, duel cards always go to this topic.
      2. The thread_id of the message/callback that triggered the duel —
         fallback for groups without the env var configured.
      3. None → posts to General.
    """
    if BSK_DUEL_THREAD_ID is not None:
        return BSK_DUEL_THREAD_ID
    msg = getattr(message_or_callback, "message", message_or_callback)
    return getattr(msg, "message_thread_id", None)


def build_bsk_keyboard(tg_id: int, active_mode: str) -> InlineKeyboardMarkup:
    modes = [("casual", "Casual"), ("ranked", "Ranked")]
    buttons = []
    for mode, label in modes:
        text = f"• {label} •" if mode == active_mode else label
        buttons.append(InlineKeyboardButton(
            text=text,
            callback_data=f"bsk:{tg_id}:{mode}",
        ))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def build_duel_panel_keyboard(mode: str = "casual") -> InlineKeyboardMarkup:
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
    ])


def pause_keyboard(duel_id: int, is_test: bool) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="⏸ Пауза", callback_data=f"bskd:pause:{duel_id}")]
    if is_test:
        row.append(InlineKeyboardButton(text="❌ Отменить", callback_data=f"bskd:test_cancel:{duel_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def resume_keyboard(duel_id: int, is_test: bool) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"bskd:resume:{duel_id}")]
    if is_test:
        row.append(InlineKeyboardButton(text="❌ Отменить", callback_data=f"bskd:test_cancel:{duel_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def get_bsk_rank(session, user_id: int, mode: str, mu_global: float) -> int | None:
    # mu_global mirrors the Python property in BskRating: 0.30·aim + 0.30·speed + 0.25·acc + 0.15·cons
    mu_expr = (
        0.30 * BskRating.mu_aim +
        0.30 * BskRating.mu_speed +
        0.25 * BskRating.mu_acc +
        0.15 * BskRating.mu_cons
    )
    total = (await session.execute(
        select(sqlfunc.count()).select_from(BskRating).where(BskRating.mode == mode)
    )).scalar() or 0
    if total == 0:
        return None
    ahead = (await session.execute(
        select(sqlfunc.count()).select_from(BskRating).where(
            BskRating.mode == mode,
            BskRating.user_id != user_id,
            mu_expr > mu_global,
        )
    )).scalar() or 0
    return 1 + ahead


async def get_bsk_data(tg_id: int, mode: str) -> dict | None:
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

        bsk_rank = await get_bsk_rank(session, user.id, mode, rating.mu_global)

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
