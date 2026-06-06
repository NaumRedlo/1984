from datetime import datetime, timedelta
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, func as sqlfunc

from config.settings import DUEL_THREAD_ID
from db.database import get_db_session
from db.models.duel import Duel
from db.models.duel_rating import DuelRating, DUEL_CONSERVATIVE_K
from db.models.user import User
from services.duel import duel_manager as dm  # noqa: F401 — re-exported for handlers
from utils.osu.resolve_user import get_any_user_by_telegram_id

_looking_for_duel: dict[int, tuple[str, datetime]] = {}
LOOKING_TIMEOUT = timedelta(minutes=15)
ONLINE_THRESHOLD = timedelta(minutes=30)


def resolve_duel_thread(message_or_callback) -> Optional[int]:
    """Return the message_thread_id where a real (non-test) duel should post.

    Priority:
      1. DUEL_THREAD_ID (env) — if set, duel cards always go to this topic.
      2. The thread_id of the message/callback that triggered the duel —
         fallback for groups without the env var configured.
      3. None → posts to General.
    """
    if DUEL_THREAD_ID is not None:
        return DUEL_THREAD_ID
    msg = getattr(message_or_callback, "message", message_or_callback)
    return getattr(msg, "message_thread_id", None)


def build_duel_keyboard(tg_id: int, active_mode: str) -> InlineKeyboardMarkup:
    modes = [("casual", "Casual"), ("ranked", "Ranked")]
    buttons = []
    for mode, label in modes:
        text = f"• {label} •" if mode == active_mode else label
        buttons.append(InlineKeyboardButton(
            text=text,
            callback_data=f"duel:{tg_id}:{mode}",
        ))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def build_duel_panel_keyboard(mode: str = "casual") -> InlineKeyboardMarkup:
    mode_casual = "• Casual •" if mode == "casual" else "Casual"
    mode_ranked = "• Ranked •" if mode == "ranked" else "Ranked"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=mode_casual, callback_data="duelpanel:mode:casual"),
            InlineKeyboardButton(text=mode_ranked, callback_data="duelpanel:mode:ranked"),
        ],
        [
            InlineKeyboardButton(text="🔍 Найти соперника", callback_data=f"duelpanel:find:{mode}"),
            InlineKeyboardButton(text="⚔️ Вызвать игрока", callback_data=f"duelpanel:pick:{mode}"),
        ],
    ])


async def get_duel_rank(session, user_id: int, mode: str, conservative: float) -> int | None:
    # Rank by the conservative TrueSkill score (mu - K*sigma), matching the
    # leaderboard and division layer.
    cons_expr = DuelRating.mu - DUEL_CONSERVATIVE_K * DuelRating.sigma
    total = (await session.execute(
        select(sqlfunc.count()).select_from(DuelRating).where(DuelRating.mode == mode)
    )).scalar() or 0
    if total == 0:
        return None
    ahead = (await session.execute(
        select(sqlfunc.count()).select_from(DuelRating).where(
            DuelRating.mode == mode,
            DuelRating.user_id != user_id,
            cons_expr > conservative,
        )
    )).scalar() or 0
    return 1 + ahead


async def _compute_recent_history(session, user_id: int, mode: str) -> dict:
    """Casual /duels relies on these instead of μ — total duels played, recent
    win-rate, current streak (+ for wins, − for losses), best streak ever, and
    the opponent + outcome of the last completed duel.

    Streaks are computed over completed duels only (in chronological order),
    so a cancelled / expired duel never breaks a streak. The recent window for
    the win-rate stat is the last 10 completed duels, which roughly matches
    how players think about "form lately"."""
    rows = (await session.execute(
        select(Duel)
        .where(
            Duel.mode == mode,
            Duel.status == "completed",
            ((Duel.player1_user_id == user_id) | (Duel.player2_user_id == user_id)),
        )
        .order_by(Duel.completed_at.asc())
    )).scalars().all()

    if not rows:
        return {
            "current_streak": 0, "best_streak": 0,
            "recent_winrate": None, "recent_count": 0,
            "lifetime_winrate": None,
            "games_7d": 0,
            "last_duel": None,
        }

    # Compute current streak (from the latest match backwards while same sign)
    # and best streak (highest run of consecutive wins ever).
    results = [1 if r.winner_user_id == user_id else -1 for r in rows]
    cur = 0
    sign = results[-1]
    for v in reversed(results):
        if v == sign:
            cur += 1
        else:
            break
    current_streak = cur * sign

    best = 0
    run = 0
    for v in results:
        if v == 1:
            run += 1
            best = max(best, run)
        else:
            run = 0

    # Recent (last 10) win rate.
    window = results[-10:]
    wins_recent = sum(1 for v in window if v == 1)
    recent_winrate = wins_recent / len(window) if window else None

    # Lifetime win-rate (drives the top-panel "WIN RATE" cell).
    wins_total = sum(1 for v in results if v == 1)
    lifetime_winrate = wins_total / len(results)

    # Activity in the last 7 days — drives the top-panel "WEEK" cell. Use a
    # tz-aware cutoff so the comparison works whether completed_at is naive
    # or aware (legacy rows can be either).
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    games_7d = 0
    for r in rows:
        ts = r.completed_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            games_7d += 1

    last = rows[-1]
    opp_id = last.player2_user_id if last.player1_user_id == user_id else last.player1_user_id
    opp = (await session.execute(
        select(User).where(User.id == opp_id)
    )).scalar_one_or_none()
    won = (last.winner_user_id == user_id)

    return {
        "current_streak": current_streak,
        "best_streak": best,
        "recent_winrate": recent_winrate,
        "recent_count": len(window),
        "lifetime_winrate": lifetime_winrate,
        "games_7d": games_7d,
        "last_duel": {
            "won": won,
            "opponent": opp.osu_username if opp else "?",
            "score": (last.player1_rounds_won, last.player2_rounds_won)
                if last.player1_user_id == user_id
                else (last.player2_rounds_won, last.player1_rounds_won),
            "completed_at": last.completed_at,
        },
    }


async def get_duel_data(tg_id: int, mode: str, chat_id: int) -> dict | None:
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id, chat_id)
        if not user or not user.osu_user_id:
            return None

        cover_data = user.cover_data

        rating_stmt = select(DuelRating).where(
            DuelRating.user_id == user.id,
            DuelRating.mode == mode,
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
            # Defaults mirror the TrueSkill env (mu0=1500, sigma0=500).
            return {
                **base,
                "mu": 1500.0,
                "sigma": 500.0,
                "conservative": 0.0,
                "peak_mu": 1500.0,
                "wins": 0,
                "losses": 0,
                "games": 0,
                "placement_matches_left": 10,
                "duel_rank": None,
                "duel_division": "",
                "current_streak": 0,
                "best_streak": 0,
                "recent_winrate": None,
                "recent_count": 0,
                "lifetime_winrate": None,
                "games_7d": 0,
                "last_duel": None,
            }

        duel_rank = await get_duel_rank(session, user.id, mode, rating.conservative)

        from utils.hp_calculator import get_division_for_conservative
        duel_division = get_division_for_conservative(rating.conservative) if mode == "ranked" else ""

        history = await _compute_recent_history(session, user.id, mode)

        return {
            **base,
            "mu": rating.mu,
            "sigma": rating.sigma,
            "conservative": rating.conservative,
            "peak_mu": rating.peak_mu,
            "wins": rating.wins,
            "losses": rating.losses,
            "games": rating.games,
            "placement_matches_left": rating.placement_matches_left,
            "duel_rank": duel_rank,
            "duel_division": duel_division,
            **history,
        }
