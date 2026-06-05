from datetime import datetime, timezone
from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot.handlers.admin.handlers import router
from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from utils.admin_check import AdminFilter
from services.hps import compute_payout
from services.bounty.notify import send_bounty_event
from utils.hp_calculator import get_rank_for_hp
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

        # Fetch usernames in one query
        user_ids = list({s.user_id for s in subs})
        urows = (await session.execute(
            select(User.id, User.osu_username).where(User.id.in_(user_ids))
        )).all()
        name_by_id = {uid: name for uid, name in urows}

    lines = ["<b>📋 Ожидающие заявки</b>"]
    for s in subs:
        username = escape_html(name_by_id.get(s.user_id, f"id:{s.user_id}"))
        acc_str = f"  {s.accuracy:.2f}%" if s.accuracy is not None else ""
        rank_str = f"  [{s.score_rank}]" if s.score_rank else ""
        mods_str = f"  +{escape_html(s.mods)}" if s.mods else ""
        lines.append(
            f"<code>#{s.id:>4}</code>  <b>{username}</b>  →  <code>{escape_html(s.bounty_id)}</code>"
            f"{acc_str}{rank_str}{mods_str}"
            f"  <i>{s.submitted_at.strftime('%d.%m %H:%M')}</i>"
        )
    lines.append(f"\n<i>rsl &lt;id&gt; для детального ревью</i>")
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

    username = escape_html(user.osu_username) if user else "Неизвестно"
    osu_id = user.osu_user_id if user else None

    # ── Score info ──
    score_lines = []
    if sub.accuracy is not None:
        acc = sub.accuracy
        req_acc = bounty.min_accuracy if bounty else None
        acc_ok = req_acc is None or acc >= req_acc
        acc_mark = "✅" if acc_ok else "❌"
        req_str = f" (мин. {req_acc}%)" if req_acc else ""
        score_lines.append(f"{acc_mark} Точность: <b>{acc:.2f}%</b>{req_str}")
    if sub.score_rank:
        score_lines.append(f"🏅 Ранг: <b>{escape_html(sub.score_rank)}</b>")
    if sub.max_combo is not None:
        score_lines.append(f"🔗 Комбо: <b>{sub.max_combo}x</b>")
    if sub.misses is not None:
        req_miss = bounty.max_misses if bounty else None
        miss_ok = req_miss is None or sub.misses <= req_miss
        miss_mark = "✅" if miss_ok else "❌"
        req_str = f" (макс. {req_miss})" if req_miss is not None else ""
        score_lines.append(f"{miss_mark} Миссов: <b>{sub.misses}</b>{req_str}")
    if sub.mods:
        req_mods = bounty.required_mods if bounty else None
        mods_str = escape_html(sub.mods)
        if req_mods:
            req_set = {m.strip().upper() for m in req_mods.replace(",", " ").split() if m.strip()}
            sub_set = {m.strip().upper() for m in sub.mods.replace(",", " ").split() if m.strip()}
            mods_ok = req_set.issubset(sub_set)
            mods_mark = "✅" if mods_ok else "❌"
            score_lines.append(f"{mods_mark} Моды: <b>+{mods_str}</b> (обяз. {escape_html(req_mods)})")
        else:
            score_lines.append(f"🎯 Моды: <b>+{mods_str}</b>")

    # ── Map info ──
    map_lines = []
    if bounty:
        map_lines.append(f"🗺 <b>{escape_html(bounty.beatmap_title)}</b>  {bounty.star_rating:.1f}★")
        map_lines.append(f"📌 Тип: <b>{escape_html(bounty.bounty_type)}</b>")

    # ── osu! profile link ──
    profile_line = ""
    if osu_id:
        profile_line = f'\n🔗 <a href="https://osu.ppy.sh/users/{osu_id}">Профиль {username}</a>'
        if bounty:
            profile_line += f'  |  <a href="https://osu.ppy.sh/beatmaps/{bounty.beatmap_id}">Карта</a>'

    lines = [
        f"<b>Заявка #{sub.id}</b>  ·  {sub.submitted_at.strftime('%d.%m.%Y %H:%M')}",
        f"👤 <b>{username}</b>  →  <code>{escape_html(sub.bounty_id)}</code>",
    ]
    if map_lines:
        lines.append("")
        lines.extend(map_lines)
    if score_lines:
        lines.append("")
        lines.extend(score_lines)
    if profile_line:
        lines.append(profile_line)
    lines.append(f"\n<b>Статус:</b> {sub.status}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Победа", callback_data=f"review_{sub.id}_win"),
            InlineKeyboardButton(text="🎯 Условие", callback_data=f"review_{sub.id}_condition"),
        ],
        [
            InlineKeyboardButton(text="〽️ Частично", callback_data=f"review_{sub.id}_partial"),
            InlineKeyboardButton(text="👟 Участие", callback_data=f"review_{sub.id}_participation"),
        ],
        [
            InlineKeyboardButton(text="✅ Победа +ZF", callback_data=f"review_{sub.id}_win_zf"),
            InlineKeyboardButton(text="🎯 Условие +ZF", callback_data=f"review_{sub.id}_condition_zf"),
        ],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"review_{sub.id}_reject")],
    ])

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


def _has_extra_challenge(mods: str | None) -> bool:
    """Score uses both HD and HR — the README-documented Extra Challenge bonus."""
    if not mods:
        return False
    tokens = {tok.strip().upper() for tok in mods.replace(",", " ").split() if tok.strip()}
    return "HD" in tokens and "HR" in tokens


@router.callback_query(F.data.regexp(r"^review_(\d+)_(win|condition|partial|participation|reject)(?:_(zf))?$"), AdminFilter())
async def review_action(callback):
    parts = callback.data.split("_")
    sub_id = int(parts[1])
    action = parts[2]
    has_zero_fifty = (len(parts) > 3 and parts[3] == "zf")

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
            sub.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
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

        hp_result = await compute_payout(
            session=session,
            user=user,
            bounty=bounty,
            submission=sub,
            result_type=result_type,
            is_first_submission=is_first,
        )

        hp_awarded = hp_result["final_hp"]
        old_hps = user.hps_points or 0

        sub.status = "approved"
        sub.result_type = result_type
        sub.hp_awarded = hp_awarded
        sub.reviewed_by = callback.from_user.id
        sub.reviewed_at = datetime.utcnow()

        user.hps_points += hp_awarded
        user.rank = get_rank_for_hp(user.hps_points)
        user.bounties_participated += 1
        user.last_active_bounty_id = str(bounty.bounty_id)
        # Anchor for B(t) bootstrap multiplier: set once on first approval.
        if user.first_approved_at is None:
            user.first_approved_at = sub.reviewed_at or datetime.utcnow()

        notify_kwargs = dict(
            chat_id=user.chat_id,
            username=user.osu_username or f"id:{user.id}",
            bounty_title=bounty.title or bounty.bounty_id,
            bounty_type=bounty.bounty_type,
            tier=bounty.tier,
            star_rating=bounty.star_rating,
            hp_awarded=hp_awarded,
            result_type=result_type,
            is_first=is_first,
            old_hps=old_hps,
            new_hps=user.hps_points,
        )

        await session.commit()

    result_names = {"win": "Победа", "condition": "Условие", "partial": "Частично", "participation": "Участие"}
    await send_bounty_event(callback.bot, **notify_kwargs)
    await callback.answer(f"Одобрена! +{hp_awarded} HP")
    await callback.message.edit_text(
        f"Заявка #{sub_id} <b>одобрена</b> — <b>{result_names.get(result_type, result_type)}</b>.\n"
        f"Начислено HP: <b>+{hp_awarded}</b>\n"
        f"Авангард (первый): {'Да' if is_first else 'Нет'}",
        parse_mode="HTML"
    )
    logger.info(f"Submission #{sub_id} approved as {result_type}, +{hp_awarded} HP by {callback.from_user.id}")
