from aiogram import Router
from aiogram.types import Message
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.duel import Duel
from db.models.user import User
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_any_user_by_telegram_id

router = Router(name="duel.history")


@router.message(TextTriggerFilter("duelhistory", "duelh"))
async def cmd_duel_history(message: Message, trigger_args: TriggerArgs):
    """duelhistory [N] — show last N completed duels (default 5)."""
    tg_id = message.from_user.id
    args = (trigger_args.args or "").strip()
    limit = 5
    try:
        if args:
            limit = max(1, min(int(args), 20))
    except ValueError:
        pass

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id, message.chat.id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        duels = (await session.execute(
            select(Duel).where(
                Duel.status == 'completed',
                (Duel.player1_user_id == user.id) | (Duel.player2_user_id == user.id),
            ).order_by(Duel.completed_at.desc()).limit(limit)
        )).scalars().all()

        if not duels:
            await message.answer("У вас ещё нет завершённых дуэлей.")
            return

        opponent_ids = {
            d.player2_user_id if d.player1_user_id == user.id else d.player1_user_id
            for d in duels
        }
        users_raw = (await session.execute(
            select(User).where(User.id.in_(opponent_ids | {user.id}))
        )).scalars().all()
        users_map = {u.id: u for u in users_raw}

    lines = [f"<b>История дуэлей</b> (последние {len(duels)}):\n"]
    for d in duels:
        is_p1 = d.player1_user_id == user.id
        opp_id = d.player2_user_id if is_p1 else d.player1_user_id
        opp = users_map.get(opp_id)
        opp_name = escape_html(opp.osu_username) if opp else "???"

        my_rounds = d.player1_rounds_won if is_p1 else d.player2_rounds_won
        opp_rounds = d.player2_rounds_won if is_p1 else d.player1_rounds_won

        won = d.winner_user_id == user.id
        draw = d.winner_user_id is None
        icon = "🤝" if draw else ("✅" if won else "❌")

        date = d.completed_at.strftime("%d.%m") if d.completed_at else "?"
        lines.append(
            f"{icon} <b>{opp_name}</b> [{d.mode}] {date}\n"
            f"   <code>{my_rounds} : {opp_rounds}</code>"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")
