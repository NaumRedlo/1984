from aiogram import Router
from aiogram.types import Message
from sqlalchemy import select

from bot.filters import TextTriggerFilter
from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.user import User
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_any_user_by_telegram_id

router = Router(name="bsk.status")


@router.message(TextTriggerFilter("bskstatus", "bskst"))
async def cmd_bsk_status(message: Message):
    """Show current active BSK duel status."""
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        duel = (await session.execute(
            select(BskDuel).where(
                BskDuel.status.in_(["pending", "accepted", "round_active"]),
                (
                    (BskDuel.player1_user_id == user.id) |
                    (BskDuel.player2_user_id == user.id)
                ),
            )
        )).scalar_one_or_none()

        if not duel:
            await message.answer("У вас нет активной BSK дуэли.")
            return

        p1 = (await session.execute(select(User).where(User.id == duel.player1_user_id))).scalar_one_or_none()
        p2 = (await session.execute(select(User).where(User.id == duel.player2_user_id))).scalar_one_or_none()

        rnd = None
        if duel.status == "round_active":
            rnd = (await session.execute(
                select(BskDuelRound)
                .where(BskDuelRound.duel_id == duel.id, BskDuelRound.status == "waiting")
                .order_by(BskDuelRound.round_number.desc())
            )).scalar_one_or_none()

    p1_name = p1.osu_username if p1 else "???"
    p2_name = p2.osu_username if p2 else "???"

    lines = [
        f"<b>BSK Дуэль</b> — {escape_html(p1_name)} vs {escape_html(p2_name)}",
        f"Режим: <b>{duel.mode.upper()}</b>  ·  Статус: <b>{duel.status}</b>",
        f"Раунд: <b>{duel.current_round}</b>  ·  Цель: <b>{duel.target_score:,} pts</b>",
        f"Счёт: <b>{int(duel.player1_total_score):,}</b> — <b>{int(duel.player2_total_score):,}</b>",
    ]

    if rnd:
        lines.append(f"\nТекущая карта: <b>{escape_html(rnd.beatmap_title or '???')}</b>")
        lines.append(f"⭐ {rnd.star_rating:.2f}  ·  https://osu.ppy.sh/b/{rnd.beatmap_id}")
        p1_done = "✅" if rnd.player1_points is not None else "⏳"
        p2_done = "✅" if rnd.player2_points is not None else "⏳"
        lines.append(f"{p1_done} {escape_html(p1_name)}  {p2_done} {escape_html(p2_name)}")

    await message.answer("\n".join(lines), parse_mode="HTML")
