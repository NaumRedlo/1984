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
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.tenant import active_tenants

logger = get_logger("tasks.bounty_weekly")


async def _setting_int(key: str) -> int | None:
    """Read a BotSettings value as int. Missing / empty / non-numeric → None.
    Used for both chat ids and forum-topic (message_thread_id) ids."""
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


async def _get_weekly_target() -> tuple[int | None, int | None]:
    """(chat_id, thread_id) for the weekly digest. thread_id is the forum
    topic /setweeklychat was run in (None → General / non-forum chat)."""
    return (
        await _setting_int("weekly_chat_id"),
        await _setting_int("weekly_thread_id"),
    )


async def _get_reminder_target() -> tuple[int | None, int | None]:
    """(chat_id, thread_id) for expiry reminders: the dedicated bounty
    channel/topic (/setbountychat) when set, else the weekly chat/topic.

    Previously reminders always went to weekly_chat_id with no topic, so they
    landed in the general channel's General topic instead of the bounty topic
    admins had configured.
    """
    bounty_chat = await _setting_int("bounty_notify_chat_id")
    if bounty_chat is not None:
        return bounty_chat, await _setting_int("bounty_notify_thread_id")
    return (
        await _setting_int("weekly_chat_id"),
        await _setting_int("weekly_thread_id"),
    )


async def _fanout_targets(cfg_chat: int | None, cfg_thread: int | None) -> list[tuple[int, int | None]]:
    """Every group to broadcast a (global) bounty digest to: all active tenants,
    plus the admin-configured chat if it has no registered users of its own.

    A configured forum topic is applied only to its own chat — other tenant
    groups receive the digest in their General topic.
    """
    async with get_db_session() as session:
        tenants = await active_tenants(session)
    targets: list[tuple[int, int | None]] = []
    seen: set[int] = set()
    for chat_id in tenants:
        targets.append((chat_id, cfg_thread if chat_id == cfg_chat else None))
        seen.add(chat_id)
    if cfg_chat and cfg_chat not in seen:
        targets.append((cfg_chat, cfg_thread))
    return targets


async def _digest_targets() -> list[tuple[int, int | None]]:
    cfg_chat, cfg_thread = await _get_weekly_target()
    return await _fanout_targets(cfg_chat, cfg_thread)


async def _reminder_targets() -> list[tuple[int, int | None]]:
    cfg_chat, cfg_thread = await _get_reminder_target()
    return await _fanout_targets(cfg_chat, cfg_thread)


async def _build_entries(bounties: list) -> list[dict]:
    async with get_db_session() as session:
        entries = []
        for b in bounties:
            sub_count = (await session.execute(
                select(func.count()).select_from(Submission).where(
                    Submission.bounty_id == b.bounty_id
                )
            )).scalar() or 0
            dl = b.deadline.strftime("%d.%m %H:%M") if b.deadline else "—"
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


async def send_weekly_digest(bot: Bot, chat_id: int, thread_id: int | None = None) -> bool:
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
        await bot.send_message(
            chat_id, "📋 Новых баунти за последние 7 дней нет.",
            message_thread_id=thread_id,
        )
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
    await bot.send_message(
        chat_id, "\n".join(lines), parse_mode="HTML", message_thread_id=thread_id,
    )
    return True


def _build_expiry_digest(bounties: list) -> str:
    """One digest card for all bounties expiring within 24h, with a soft length
    cap (Telegram hard-limits at 4096 chars)."""
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
    return "\n".join(lines)


async def send_expiry_reminders(bot: Bot, chat_id: int, thread_id: int | None = None) -> int:
    """Single-chat expiry digest (admin path & tests). Delegates to the
    multi-target fan-out with a one-element target list."""
    return await send_expiry_reminders_multi(bot, [(chat_id, thread_id)])


async def send_expiry_reminders_multi(
    bot: Bot, targets: list[tuple[int, int | None]],
) -> int:
    """Send ONE digest of all bounties expiring within 24h to EACH target group,
    then stamp `reminder_sent` once. Returns the number of bounties included
    (0 → nothing sent).

    Bounty content is global, so every active tenant gets the same digest. The
    `reminder_sent` flag is stamped once after the fan-out — not per chat — so a
    later manual bounty still gets its own digest while re-runs stay quiet.

    Auto-bounties all inherit the weekly pool's deadline, so they cross the 24h
    line together; collapsing them into a single card avoids a burst of dozens.
    """
    targets = [(c, t) for (c, t) in targets if c]
    if not targets:
        return 0

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

        text = _build_expiry_digest(list(bounties))

        sent_any = False
        for chat_id, thread_id in targets:
            try:
                await bot.send_message(
                    chat_id, text, parse_mode="HTML", message_thread_id=thread_id,
                )
                sent_any = True
            except Exception:
                logger.error(
                    f"Failed to send expiry reminder digest to {chat_id}", exc_info=True
                )

        if not sent_any:
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

        targets = await _digest_targets()
        if not targets:
            logger.warning("Weekly digest: no active tenant / configured chat, skipping")
            continue

        for chat_id, thread_id in targets:
            try:
                await send_weekly_digest(bot, chat_id, thread_id)
                logger.info(f"Weekly digest sent to {chat_id} (thread {thread_id})")
            except Exception:
                logger.error(f"Weekly digest send failed for {chat_id}", exc_info=True)


async def expiry_reminder_loop(bot: Bot, shutdown_event: asyncio.Event) -> None:
    CHECK_INTERVAL = 3600  # 1 hour

    while not shutdown_event.is_set():
        targets = await _reminder_targets()
        if targets:
            try:
                n = await send_expiry_reminders_multi(bot, targets)
                if n:
                    logger.info(
                        f"Sent {n} expiry reminder(s) to {len(targets)} chat(s)"
                    )
            except Exception:
                logger.error("Expiry reminder iteration failed", exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=CHECK_INTERVAL)
            break
        except asyncio.TimeoutError:
            continue
