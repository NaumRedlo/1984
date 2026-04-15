from datetime import datetime

from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot.handlers.admin.handlers import router
from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from utils.admin_check import AdminFilter
from utils.hp_calculator import calculate_hps, get_rank_for_hp
from utils.osu.helpers import get_community_stats
from utils.formatting.text import escape_html, format_error
from utils.logger import get_logger

logger = get_logger("handlers.admin.review")


@router.message(TextTriggerFilter("review"), AdminFilter())
async def review_command(message, trigger_args: TriggerArgs = None):
    async with get_db_session() as session:
        stmt = select(Submission).where(Submission.status == "pending")
        subs = (await session.execute(stmt)).scalars().all()

    if not subs:
        await message.answer("Нет заявок на рассмотрение.")
        return

    lines = ["<b>Ожидающие заявки:</b>", "═" * 28]
    for s in subs:
        lines.append(
            f"<b>#{s.id}</b> | Баунти: {escape_html(s.bounty_id)} | "
            f"Игрок: {s.user_id} | {s.submitted_at.strftime('%d.%m %H:%M')}"
        )
    lines.append(f"\nИспользуйте reviewselect &lt;id&gt; (или rsl &lt;id&gt;) для ревью.")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("reviewselect", "rsl"), AdminFilter())
async def reviewselect_command(message, trigger_args: TriggerArgs):
    args = trigger_args.args
    if not args or not args.strip().isdigit():
        await message.answer(format_error("Использование: reviewselect <submission_id>"))
        return
    await _review_select(message, int(args.strip()))


async def _review_select(message, sub_id: int):
    async with get_db_session() as session:
        stmt = select(Submission).where(Submission.id == sub_id)
        sub = (await session.execute(stmt)).scalar_one_or_none()
        if not sub:
            await message.answer(format_error(f"Заявка #{sub_id} не найдена."))
            return

        b_stmt = select(Bounty).where(Bounty.bounty_id == sub.bounty_id)
        bounty = (await session.execute(b_stmt)).scalar_one_or_none()

        u_stmt = select(User).where(User.id == sub.user_id)
        user = (await session.execute(u_stmt)).scalar_one_or_none()

    username = user.osu_username if user else "Неизвестно"

    lines = [
        f"<b>Заявка #{sub.id}</b>",
        "═" * 28,
        f"<b>Баунти:</b> {escape_html(sub.bounty_id)}",
        f"<b>Игрок:</b> {escape_html(username)} (TG: {sub.telegram_id})",
    ]
    if sub.accuracy is not None:
        lines.append(f"<b>Точность:</b> {sub.accuracy:.2f}%")
    if sub.max_combo is not None:
        lines.append(f"<b>Комбо:</b> {sub.max_combo}x")
    if sub.misses is not None:
        lines.append(f"<b>Миссов:</b> {sub.misses}")
    if sub.mods:
        lines.append(f"<b>Моды:</b> {escape_html(sub.mods)}")
    if sub.score_rank:
        lines.append(f"<b>Ранг:</b> {escape_html(sub.score_rank)}")

    if bounty:
        lines.extend([
            "═" * 28,
            "<b>Условия баунти:</b>",
            f"Мин. точность: {bounty.min_accuracy}%" if bounty.min_accuracy else "Мин. точность: —",
            f"Обяз. моды: {bounty.required_mods}" if bounty.required_mods else "Обяз. моды: —",
            f"Макс. миссов: {bounty.max_misses}" if bounty.max_misses is not None else "Макс. миссов: —",
        ])

    lines.append(f"\n<b>Статус:</b> {sub.status}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Победа", callback_data=f"review_{sub.id}_win"),
            InlineKeyboardButton(text="Условие", callback_data=f"review_{sub.id}_condition"),
        ],
        [
            InlineKeyboardButton(text="Частично", callback_data=f"review_{sub.id}_partial"),
            InlineKeyboardButton(text="Участие", callback_data=f"review_{sub.id}_participation"),
        ],
        [InlineKeyboardButton(text="Отклонить", callback_data=f"review_{sub.id}_reject")],
    ])

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.regexp(r"^review_(\d+)_(win|condition|partial|participation|reject)$"), AdminFilter())
async def review_action(callback):
    parts = callback.data.split("_")
    sub_id = int(parts[1])
    action = parts[2]

    async with get_db_session() as session:
        stmt = select(Submission).where(Submission.id == sub_id)
        sub = (await session.execute(stmt)).scalar_one_or_none()
        if not sub:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return

        if sub.status != "pending":
            await callback.answer("Уже рассмотрена.", show_alert=True)
            return

        if action == "reject":
            sub.status = "rejected"
            sub.reviewed_by = callback.from_user.id
            sub.reviewed_at = datetime.utcnow()
            await session.commit()
            await callback.answer("Отклонена")
            await callback.message.edit_text(
                f"Заявка #{sub_id} <b>отклонена</b>.",
                parse_mode="HTML"
            )
            return

        result_type = action

        b_stmt = select(Bounty).where(Bounty.bounty_id == sub.bounty_id)
        bounty = (await session.execute(b_stmt)).scalar_one_or_none()
        if not bounty:
            await callback.answer("Баунти не найден.", show_alert=True)
            return

        u_stmt = select(User).where(User.id == sub.user_id)
        user = (await session.execute(u_stmt)).scalar_one_or_none()
        if not user:
            await callback.answer("Игрок не найден.", show_alert=True)
            return

        first_stmt = select(Submission).where(
            Submission.bounty_id == sub.bounty_id,
            Submission.status == "approved"
        )
        first_result = (await session.execute(first_stmt)).first()
        is_first = first_result is None

        community_stats = await get_community_stats(session)

        hp_result = calculate_hps(
            result_type=result_type,
            star_rating=bounty.star_rating,
            drain_time_seconds=bounty.drain_time,
            player_pp=user.player_pp or 0,
            community_stats=community_stats,
            accuracy=sub.accuracy or 0.0,
            is_first_submission=is_first,
            has_zero_fifty=False,
            extra_challenge=False,
            cs=bounty.cs or 0.0,
            od=bounty.od or 0.0,
            ar=bounty.ar or 0.0,
            hp_drain=bounty.hp_drain or 0.0,
            bpm=bounty.bpm or 0.0,
            max_combo=bounty.max_combo or 0,
        )

        hp_awarded = hp_result["final_hp"]

        sub.status = "approved"
        sub.result_type = result_type
        sub.hp_awarded = hp_awarded
        sub.reviewed_by = callback.from_user.id
        sub.reviewed_at = datetime.utcnow()

        user.hps_points += hp_awarded
        user.rank = get_rank_for_hp(user.hps_points)
        user.bounties_participated += 1
        user.last_active_bounty_id = str(bounty.bounty_id)

        await session.commit()

    result_names = {"win": "Победа", "condition": "Условие", "partial": "Частично", "participation": "Участие"}
    await callback.answer(f"Одобрена! +{hp_awarded} HP")
    await callback.message.edit_text(
        f"Заявка #{sub_id} <b>одобрена</b> — <b>{result_names.get(result_type, result_type)}</b>.\n"
        f"Начислено HP: <b>+{hp_awarded}</b>\n"
        f"Авангард (первый): {'Да' if is_first else 'Нет'}",
        parse_mode="HTML"
    )
    logger.info(f"Submission #{sub_id} approved as {result_type}, +{hp_awarded} HP by {callback.from_user.id}")
