from aiogram import Router
from aiogram.types import Message
from sqlalchemy import select

from bot.filters import TextTriggerFilter
from db.database import get_db_session
from db.models.duel import Duel
from db.models.duel_round import DuelRound
from db.models.user import User
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_any_user_by_telegram_id

router = Router(name="duel.status")


@router.message(TextTriggerFilter("duelstatus", "duelst"))
async def cmd_duel_status(message: Message):
    """Show current active DUEL duel status."""
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        duel = (await session.execute(
            select(Duel).where(
                Duel.status.in_(["pending", "accepted", "round_active"]),
                (
                    (Duel.player1_user_id == user.id) |
                    (Duel.player2_user_id == user.id)
                ),
            )
        )).scalar_one_or_none()

        if not duel:
            await message.answer("У вас нет активной DUEL дуэли.")
            return

        p1 = (await session.execute(select(User).where(User.id == duel.player1_user_id))).scalar_one_or_none()
        p2 = (await session.execute(select(User).where(User.id == duel.player2_user_id))).scalar_one_or_none()

        rnd = None
        if duel.status == "round_active":
            rnd = (await session.execute(
                select(DuelRound)
                .where(DuelRound.duel_id == duel.id, DuelRound.status == "playing")
                .order_by(DuelRound.round_number.desc())
            )).scalar_one_or_none()

    p1_name = p1.osu_username if p1 else "???"
    p2_name = p2.osu_username if p2 else "???"

    lines = [
        f"<b>Дуэль</b> — {escape_html(p1_name)} vs {escape_html(p2_name)}",
        f"Режим: <b>{duel.mode.upper()}</b>  ·  Статус: <b>{duel.status}</b>",
        f"Формат: <b>Bo{duel.total_rounds}</b> (до {duel.win_target})  ·  "
        f"Раунд: <b>{duel.current_round + 1}</b>",
        f"Счёт: <b>{duel.player1_rounds_won} : {duel.player2_rounds_won}</b>",
    ]

    if rnd:
        lines.append(f"\nТекущая карта: <b>{escape_html(rnd.beatmap_title or '???')}</b>")
        lines.append(f"⭐ {rnd.star_rating:.2f}  ·  https://osu.ppy.sh/b/{rnd.beatmap_id}")

    await message.answer("\n".join(lines), parse_mode="HTML")
