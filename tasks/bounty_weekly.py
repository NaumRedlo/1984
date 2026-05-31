"""Background tasks for weekly bounty digest and expiry reminders.

Two loops run concurrently:
- weekly_digest_loop   — every Monday at 10:00 (TIMEZONE), sends new bounties
                         created in the last 7 days as a bountylist card.
- expiry_reminder_loop — every hour, sends a reminder for bounties whose
                         deadline falls within the next 24 hours and for which
                         a reminder hasn't been sent yet.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from sqlalchemy import select, func, update

from config.settings import TIMEZONE
from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.bot_settings import BotSettings
from db.models.user import User
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger("tasks.bounty_weekly")


async def _get_setting_chat_id(key: str) -> int | None:
    async with get_db_session() as session:
        row = (await session.execute(
            select(BotSettings).where(BotSettings.key == key)
        )).scalar_one_or_none()
        if row and row.value:
            try:
                return int(row.value)
            except ValueError:
                return None
    return None


async def _get_weekly_chat_id() -> int | None:
    return await _get_setting_chat_id("weekly_chat_id")


async def _get_reminder_chat_id() -> int | None:
    """Chat for expiry reminders: the dedicated bounty channel
    (/setbountychat) when set, else the weekly chat as a fallback.

    Previously reminders always went to weekly_chat_id, so they landed in the
    general announcements channel instead of the bounty channel admins had
    configured for bounty events.
    """
    return (
        await _get_setting_chat_id("bounty_notify_chat_id")
        or await _get_setting_chat_id("weekly_chat_id")
    )


async def _build_entries(bounties: list) -> list[dict]:
    async with get_db_session() as session:
        host_ids = {b.created_by for b in bounties}
        hosts_by_tg: dict = {}
        if host_ids:
            host_rows = (await session.execute(
                select(User).where(User.telegram_id.in_(host_ids))
            )).scalars().all()
            hosts_by_tg = {u.telegram_id: u for u in host_rows}

        entries = []
        for b in bounties:
            sub_count = (await session.execute(
                select(func.count()).select_from(Submission).where(
                    Submission.bounty_id == b.bounty_id
                )
            )).scalar() or 0
            dl = b.deadline.strftime("%d.%m %H:%M") if b.deadline else "—"
            host = hosts_by_tg.get(b.created_by)
            entries.append({
                "bounty_id": b.bounty_id,
                "bounty_type": b.bounty_type or "First FC",
                "tier": b.tier,
                "title": b.title,
                "beatmap_title": b.beatmap_title,
                "star_rating": b.star_rating,
                "deadline": dl,
                "participant_count": sub_count,
                "max_participants": b.max_participants,
            })
        return entries


async def send_weekly_digest(bot: Bot, chat_id: int) -> bool:
    """Fetch bounties created in the last 7 days and send the list card."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz).replace(tzinfo=None)
    week_ago = now - timedelta(days=7)

    async with get_db_session() as session:
        stmt = (
            select(Bounty)
            .where(Bounty.status == "active", Bounty.created_at >= week_ago)
            .order_by(Bounty.created_at.desc())
        )
        bounties = (await session.execute(stmt)).scalars().all()

    if not bounties:
        await bot.send_message(chat_id, "📋 Новых баунти за последние 7 дней нет.")
        return True

    entries = await _build_entries(list(bounties))
    lines = [f"📋 <b>Новые баунти за неделю</b> — {len(bounties)} шт.\n"]
    for e in entries:
        tier = f"[Tier {e['tier']}] " if e.get("tier") else ""
        dl = e.get("deadline") or "—"
        lines.append(
            f"• {tier}<b>#{escape_html(e['bounty_id'])}</b> {escape_html(e['title'])}\n"
            f"  {e.get('star_rating', 0):.2f}★ | Дедлайн: {dl}"
        )
    await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
    return True


async def send_expiry_reminders(bot: Bot, chat_id: int) -> int:
    """Send ONE digest for all bounties expiring within 24h. Returns the
    number of bounties included (0 → nothing sent).

    Auto-bounties all inherit the weekly pool's deadline, so every one of
    them crosses the 24h line in the same hourly tick. The old per-bounty
    message meant a burst of dozens of identical alerts at once. We collapse
    them into a single card and still stamp `reminder_sent` on each, so the
    next tick stays quiet and a later manual bounty gets its own digest.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    in_24h = now + timedelta(hours=24)

    async with get_db_session() as session:
        stmt = (
            select(Bounty)
            .where(
                Bounty.status == "active",
                Bounty.deadline.is_not(None),
                Bounty.deadline > now,
                Bounty.deadline <= in_24h,
                Bounty.reminder_sent.is_(False),
            )
            .order_by(Bounty.deadline.asc())
        )
        bounties = (await session.execute(stmt)).scalars().all()
        if not bounties:
            return 0

        # Build the digest with a soft length cap (Telegram hard-limits at
        # 4096 chars). Undisplayed bounties are still marked reminded.
        MAX_CHARS = 3500
        header = (
            f"⏰ <b>Скоро дедлайн</b> — {len(bounties)} "
            f"баунти истекают в ближайшие 24ч\n"
        )
        lines = [header]
        total = len(header)
        shown = 0
        for b in bounties:
            dl = b.deadline.strftime("%d.%m %H:%M UTC") if b.deadline else "—"
            tier = f"[{escape_html(b.tier)}] " if b.tier and b.tier != "Open" else ""
            line = (
                f"• {tier}<b>#{escape_html(b.bounty_id)}</b> "
                f"{escape_html(b.title)}\n  Дедлайн: {dl}"
            )
            if total + len(line) + 1 > MAX_CHARS:
                lines.append(f"…и ещё {len(bounties) - shown}")
                break
            lines.append(line)
            total += len(line) + 1
            shown += 1

        try:
            await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
        except Exception:
            logger.error("Failed to send expiry reminder digest", exc_info=True)
            return 0

        await session.execute(
            update(Bounty)
            .where(Bounty.id.in_([b.id for b in bounties]))
            .values(reminder_sent=True)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return len(bounties)


async def weekly_digest_loop(bot: Bot, shutdown_event: asyncio.Event) -> None:
    tz = ZoneInfo(TIMEZONE)

    while not shutdown_event.is_set():
        now = datetime.now(tz)
        # Next Monday 10:00
        days_until_monday = (7 - now.weekday()) % 7 or 7
        target = (now + timedelta(days=days_until_monday)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        wait_seconds = (target - now).total_seconds()
        logger.info(f"Weekly digest: next send in {wait_seconds/3600:.1f}h at {target.strftime('%Y-%m-%d %H:%M %Z')}")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=wait_seconds)
            break
        except asyncio.TimeoutError:
            pass

        if shutdown_event.is_set():
            break

        chat_id = await _get_weekly_chat_id()
        if not chat_id:
            logger.warning("Weekly digest: weekly_chat_id not set, skipping")
            continue

        try:
            await send_weekly_digest(bot, chat_id)
            logger.info(f"Weekly digest sent to {chat_id}")
        except Exception:
            logger.error("Weekly digest send failed", exc_info=True)


async def expiry_reminder_loop(bot: Bot, shutdown_event: asyncio.Event) -> None:
    CHECK_INTERVAL = 3600  # 1 hour

    while not shutdown_event.is_set():
        chat_id = await _get_reminder_chat_id()
        if chat_id:
            try:
                n = await send_expiry_reminders(bot, chat_id)
                if n:
                    logger.info(f"Sent {n} expiry reminder(s) to {chat_id}")
            except Exception:
                logger.error("Expiry reminder iteration failed", exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=CHECK_INTERVAL)
            break
        except asyncio.TimeoutError:
            continue
