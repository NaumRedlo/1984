from utils.timeutils import utcnow

from aiogram import Router, types
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html, format_error, format_success
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_bounty_misc")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


@router.message(TextTriggerFilter("bountyclose", "bcl"))
async def bountyclose_command(message: types.Message, trigger_args: TriggerArgs):
    bounty_id = trigger_args.args
    if not bounty_id:
        await message.answer(format_error("Использование: bountyclose <bounty_id>"))
        return

    async with get_db_session() as session:
        stmt = select(Bounty).where(Bounty.bounty_id == bounty_id.strip())
        bounty = (await session.execute(stmt)).scalar_one_or_none()
        if not bounty:
            await message.answer(format_error(f"Баунти {escape_html(bounty_id)} не найден."), parse_mode="HTML")
            return
        if bounty.status == "closed":
            await message.answer(format_error("Баунти уже закрыт."))
            return

        bounty.status = "closed"
        bounty.closed_at = utcnow()
        await session.commit()

    await message.answer(format_success(f"Баунти <b>{escape_html(bounty_id)}</b> закрыт."), parse_mode="HTML")
    logger.info(f"Bounty {bounty_id} closed by {message.from_user.id}")


@router.message(TextTriggerFilter("bountydelete", "bdl"))
async def bountydelete_command(message: types.Message, trigger_args: TriggerArgs):
    bounty_id = trigger_args.args
    if not bounty_id:
        await message.answer(format_error("Использование: bountydelete <bounty_id>"))
        return

    async with get_db_session() as session:
        stmt = select(Bounty).where(Bounty.bounty_id == bounty_id.strip())
        bounty = (await session.execute(stmt)).scalar_one_or_none()
        if not bounty:
            await message.answer(format_error(f"Баунти {escape_html(bounty_id)} не найден."), parse_mode="HTML")
            return

        sub_stmt = select(Submission).where(Submission.bounty_id == bounty_id.strip())
        subs = (await session.execute(sub_stmt)).scalars().all()
        for s in subs:
            await session.delete(s)
        await session.delete(bounty)
        await session.commit()

    await message.answer(format_success(f"Баунти <b>{escape_html(bounty_id)}</b> удалён."), parse_mode="HTML")
    logger.info(f"Bounty {bounty_id} deleted by {message.from_user.id}")


@router.message(TextTriggerFilter("regenpool", "regenerate_pool"))
async def regenpool_command(message: types.Message, trigger_args: TriggerArgs):
    """Manually regenerate the weekly bounty pool.

    Closes the currently-active WeeklyBountyPool and all its auto-bounties,
    snapshots user tiers, generates a fresh pool (SLOTS_PER_TIER × 4 tiers).
    Manual bounties (source='manual') are NEVER touched.

    Requires explicit confirmation: /regenpool confirm
    """
    from services.bounty.weekly_generator import SLOTS_PER_TIER

    arg = (trigger_args.args or "").strip().lower()
    total_slots = SLOTS_PER_TIER * 4

    if arg != "confirm":
        await message.answer(
            "⚠️ <b>Регенерация недельного пула баунти</b>\n\n"
            "Это действие:\n"
            "  • закроет текущий активный пул (auto-баунти → expired)\n"
            "  • пересчитает weekly_tier для всех игроков\n"
            f"  • сгенерирует новый пул из {total_slots} баунти "
            f"({SLOTS_PER_TIER}×C + {SLOTS_PER_TIER}×B + {SLOTS_PER_TIER}×A + "
            f"{SLOTS_PER_TIER}×Open)\n\n"
            "<i>Manual-баунти не затрагиваются.</i>\n\n"
            "Чтобы подтвердить, отправьте:\n"
            "<code>/regenpool confirm</code>",
            parse_mode="HTML",
        )
        return

    wait = await message.answer("⏳ Генерирую новый недельный пул…")

    try:
        from services.bounty.weekly_generator import generate_weekly_pool
        from sqlalchemy import func
        from db.models.bounty import Bounty

        async with get_db_session() as session:
            # Explicit admin rotation — always regenerate.
            pool = await generate_weekly_pool(session, force=True)
            await session.commit()

            # Tally results per tier for the confirmation message
            counts_stmt = (
                select(Bounty.tier, func.count(Bounty.bounty_id))
                .where(Bounty.week_id == pool.id, Bounty.source == "auto")
                .group_by(Bounty.tier)
            )
            counts = dict((await session.execute(counts_stmt)).all())

        lines = [
            "✅ <b>Пул сгенерирован</b>",
            f"  • week_id = <code>{pool.id}</code>",
            f"  • week_number = <b>{pool.week_number}</b>",
            f"  • действует до: {pool.ends_at.strftime('%d.%m.%Y %H:%M UTC')}",
            "",
            "<b>Распределение по тирам:</b>",
        ]
        for tier in ("C", "B", "A", "Open"):
            count = counts.get(tier, 0)
            emoji = {"C": "🟢", "B": "🟡", "A": "🔴", "Open": "⚪"}[tier]
            warn = " ⚠ <i>неполный</i>" if 0 < count < SLOTS_PER_TIER else (" ❌ <i>пусто</i>" if count == 0 else "")
            lines.append(f"  {emoji} [Tier {tier}]: <b>{count}</b>/{SLOTS_PER_TIER}{warn}")

        total = sum(counts.values())
        lines.append(f"\n<b>Итого:</b> {total} баунти")
        lines.append("\nПросмотр: bli")

        await wait.edit_text("\n".join(lines), parse_mode="HTML")
        logger.info(
            f"Bounty pool regenerated by {message.from_user.id}: "
            f"week={pool.week_number} total={total} counts={counts}"
        )
    except Exception as e:
        logger.error(f"regenpool failed: {e}", exc_info=True)
        await wait.edit_text(
            format_error(f"Ошибка генерации пула: {escape_html(str(e))}"),
            parse_mode="HTML",
        )
