from datetime import datetime

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
        bounty.closed_at = datetime.utcnow()
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
