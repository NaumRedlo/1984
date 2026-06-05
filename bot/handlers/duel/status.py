from aiogram import Router
from aiogram.types import BufferedInputFile, Message
from sqlalchemy import select

from bot.filters import TextTriggerFilter
from db.database import get_db_session
from db.models.duel import Duel
from services.duel.status_card import assemble_status_data
from services.image import card_renderer
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.osu.resolve_user import get_any_user_by_telegram_id

logger = get_logger("duel.status")
router = Router(name="duel.status")


def _text_fallback(data: dict) -> str:
    p1, p2 = data["p1"], data["p2"]
    s1, s2 = data["score"]
    lines = [
        f"<b>Дуэль</b> — {escape_html(p1['username'])} vs {escape_html(p2['username'])}",
        f"Режим: <b>{data['mode'].upper()}</b>  ·  Статус: <b>{data['status']}</b>",
        f"Формат: <b>Bo{data['total_rounds']}</b> (до {data['win_target']})  ·  "
        f"Раунд: <b>{data['current_round'] + 1}</b>",
        f"Счёт: <b>{s1} : {s2}</b>",
    ]
    cur = data.get("current_map")
    if cur:
        lines.append(f"\nТекущая карта: <b>{escape_html(str(cur.get('title', '???')))}</b>")
        lines.append(f"⭐ {float(cur.get('star_rating', 0.0)):.2f}  ·  "
                     f"https://osu.ppy.sh/b/{cur.get('beatmap_id')}")
    return "\n".join(lines)


@router.message(TextTriggerFilter("duelstatus", "duelst"))
async def cmd_duel_status(message: Message):
    """Show the current active DUEL duel as a head-to-head status card."""
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id, message.chat.id)
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

        data = await assemble_status_data(session, duel)

    try:
        img_buf = await card_renderer.generate_duel_status_card_async(data)
        await message.answer_photo(
            BufferedInputFile(img_buf.read(), filename="duel_status.png"),
        )
    except Exception as e:
        logger.error(f"duel status card render failed: {e}", exc_info=True)
        await message.answer(_text_fallback(data), parse_mode="HTML")
