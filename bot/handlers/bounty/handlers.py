import json as _json
from datetime import datetime, timezone
from aiogram import Router, types
from aiogram.types import BufferedInputFile
from sqlalchemy import select, func, distinct

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from utils.hp_calculator import (
    MapInfo,
    PlayerSkill,
    RANK_THRESHOLDS,
    RESULT_TYPE_MULTIPLIER,
    ScoreStats,
    calculate_hps,
)
from utils.osu.resolve_user import get_registered_user
from services.hps.duel_user_skill import compute_duel_user_skill
from services.hps.payout import _map_info_for_bounty  # internal: same logic everyone uses
from services.image.core import CardRenderer
from utils.logger import get_logger
from utils.formatting.text import escape_html, format_error, format_success
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.bounty.nav import store_bounty_nav, bounty_tier_keyboard, bounty_detail_keyboard

logger = get_logger(__name__)

router = Router(name="bounty")

RANK_ORDER = [r[1] for r in reversed(RANK_THRESHOLDS)]  # Candidate, Member, ...


def _rank_meets_minimum(player_rank: str, min_rank: str) -> bool:
    try:
        player_idx = RANK_ORDER.index(player_rank)
        min_idx = RANK_ORDER.index(min_rank)
        return player_idx >= min_idx
    except ValueError:
        return False


def _format_conditions_latin(bounty) -> str:
    """Compact single-line Latin string for the tier card (Torus-safe, ASCII only)."""
    parts = []
    if bounty.max_misses == 0:
        parts.append("FC")
    elif bounty.max_misses is not None:
        parts.append(f"<={bounty.max_misses} miss")
    if bounty.min_accuracy is not None:
        a = float(bounty.min_accuracy)
        parts.append("SS" if a >= 100 else f"Acc {a:.1f}+")
    # Mods are rendered as icon badges on the card (required_mods field), not
    # baked into this text — so they're intentionally omitted here.
    if bounty.conditions:
        try:
            jc = _json.loads(bounty.conditions)
            if isinstance(jc, dict):
                if "max_ur" in jc:
                    parts.append(f"UR<={jc['max_ur']}ms")
                if "min_combo_pct" in jc:
                    pct = float(jc["min_combo_pct"]) * 100
                    parts.append(f"Cmb>={pct:.0f}%")
        except Exception:
            pass
    if bounty.min_rank:
        parts.append(f"Rank>={bounty.min_rank}")
    return "   ".join(parts)


def _format_conditions_compact(bounty) -> list[str]:
    """Format bounty conditions as compact emoji strings for the card."""
    lines = []
    if bounty.min_accuracy is not None:
        lines.append(f"🎯 Точность ≥ {bounty.min_accuracy}%")
    if bounty.max_misses is not None:
        lines.append("FC (0 миссов)" if bounty.max_misses == 0 else f"❌ Миссов ≤ {bounty.max_misses}")
    if bounty.required_mods:
        lines.append(f"🎚 Моды: {bounty.required_mods}")
    if bounty.conditions:
        try:
            jc = _json.loads(bounty.conditions)
            if isinstance(jc, dict):
                if "max_ur" in jc:
                    lines.append(f"⏱ UR ≤ {jc['max_ur']} ms")
                if "min_combo_pct" in jc:
                    pct = float(jc["min_combo_pct"]) * 100
                    lines.append(f"🔗 Комбо ≥ {pct:.0f}%")
        except Exception:
            pass
    if bounty.min_rank:
        lines.append(f"🥇 Ранг ≥ {bounty.min_rank}")
    return lines or ["Без ограничений"]


async def _do_accept(session, user, bounty_id: str) -> tuple[bool, str]:
    """Core accept logic shared by text command and inline button."""
    stmt = select(Bounty).where(Bounty.bounty_id == bounty_id)
    bounty = (await session.execute(stmt)).scalar_one_or_none()
    if not bounty:
        return False, f"Баунти {escape_html(bounty_id)} не найден."

    if bounty.status != "active":
        return False, f"Баунти имеет статус «{bounty.status}», приём закрыт."

    now = datetime.utcnow()
    if bounty.deadline and bounty.deadline < now:
        bounty.status = "expired"
        bounty.closed_at = now
        await session.commit()
        return False, "Дедлайн баунти истёк."

    # Block re-entry if already in progress or approved.
    dup_stmt = select(Submission).where(
        Submission.bounty_id == bounty_id,
        Submission.user_id == user.id,
        Submission.status.in_(("approved", "pending", "tracking")),
    )
    existing = (await session.execute(dup_stmt)).scalar_one_or_none()
    if existing:
        msgs = {
            "approved": "У вас уже есть одобренная заявка.",
            "pending": f"Заявка #{existing.id} ждёт рассмотрения.",
            "tracking": "Вы уже приняли этот баунти.",
        }
        return False, msgs.get(existing.status, "Дубликат.")

    # Per-bounty attempt cap and weekly claim cap removed (feedback,
    # 2026-05-29): bounties are fixed-payout orders — first valid submission
    # closes them, retries are tracked silently in `submissions` for abuse
    # detection but no longer block claims.

    submission = Submission(
        bounty_id=bounty_id,
        user_id=user.id,
        telegram_id=user.telegram_id,
        status="tracking",
    )
    session.add(submission)
    await session.commit()
    return True, "Принят! Скоры на этой карте отслеживаются автоматически."


# /bountylist (/bli)

_TIER_ORDER = ("C", "B", "A", "Open")


@router.message(TextTriggerFilter("bountylist", "bli"))
async def bountylist_command(message: types.Message, trigger_args: TriggerArgs = None):
    now = datetime.utcnow()
    async with get_db_session() as session:
        stmt = (
            select(Bounty)
            .where(Bounty.status == "active")
            .order_by(Bounty.tier.asc().nulls_last(), Bounty.created_at.desc())
        )
        bounties = (await session.execute(stmt)).scalars().all()
        active = [b for b in bounties if not (b.deadline and b.deadline < now)]

        if not active:
            await message.answer("Нет активных баунти.")
            return

        from sqlalchemy import func as _func
        counts_stmt = (
            select(Submission.bounty_id, _func.count(Submission.id))
            .where(Submission.bounty_id.in_([b.bounty_id for b in active]))
            .group_by(Submission.bounty_id)
        )
        sub_counts = dict((await session.execute(counts_stmt)).all())

    # Build entry dicts and group by tier; Open is populated for manual bounties too
    by_tier: dict = {t: [] for t in _TIER_ORDER}
    for b in active:
        dl = b.deadline.strftime("%d.%m %H:%M") if b.deadline else "--"
        sub_count = sub_counts.get(b.bounty_id, 0)
        tier = b.tier if b.tier in _TIER_ORDER else "Open"
        by_tier[tier].append({
            "bounty_id": b.bounty_id,
            "bounty_type": b.bounty_type or "First FC",
            "tier": tier,
            "title": b.title,
            "beatmap_title": b.beatmap_title,
            "beatmapset_id": b.beatmapset_id,
            "star_rating": b.star_rating,
            "drain_time": b.drain_time,
            "mapper_name": b.mapper_name,
            "deadline": dl,
            "participant_count": sub_count,
            "max_participants": b.max_participants,
            "conditions": _format_conditions_compact(b),
            "conditions_latin": _format_conditions_latin(b),
            "required_mods": b.required_mods,
        })

    uid = message.from_user.id
    store_bounty_nav(uid, by_tier)

    # Open tier shown first; fall back to whichever tier has entries
    default_tier = next(
        (t for t in ("Open", "C", "B", "A") if by_tier.get(t)),
        "Open",
    )

    wait = await message.answer("⏳ Генерирую карточку…")
    renderer = CardRenderer()
    try:
        buf = await renderer.generate_bounty_tier_card_async(
            default_tier, by_tier[default_tier]
        )
    except Exception as e:
        logger.error(f"bountylist card render failed: {e}", exc_info=True)
        await wait.edit_text(format_error("Ошибка генерации карточки."), parse_mode="HTML")
        return

    keyboard = bounty_tier_keyboard(uid, default_tier, 0, by_tier)
    await wait.delete()
    await message.answer_photo(
        photo=BufferedInputFile(buf.getvalue(), filename="bounty.jpg"),
        reply_markup=keyboard,
    )


# /bountydetails (/bde)

@router.message(TextTriggerFilter("bountydetails", "bde"))
async def bountydetails_command(message: types.Message, trigger_args: TriggerArgs):
    bounty_id = trigger_args.args
    if not bounty_id:
        await message.answer(format_error("Использование: bountydetails <bounty_id>"))
        return

    async with get_db_session() as session:
        stmt = select(Bounty).where(Bounty.bounty_id == bounty_id.strip())
        bounty = (await session.execute(stmt)).scalar_one_or_none()
        if not bounty:
            await message.answer(
                format_error(f"Баунти {escape_html(bounty_id)} не найден."),
                parse_mode="HTML",
            )
            return

        sub_count_stmt = select(func.count()).select_from(Submission).where(
            Submission.bounty_id == bounty.bounty_id
        )
        sub_count = (await session.execute(sub_count_stmt)).scalar() or 0

        dl = bounty.deadline.strftime("%d.%m.%Y %H:%M") if bounty.deadline else "—"

        hps_preview_hp = None
        user = await get_registered_user(session, message.from_user.id)
        if user:
            map_info, _ = await _map_info_for_bounty(bounty, session)
            skill = await compute_duel_user_skill(user, session)
            preview = calculate_hps(
                result_type="win",
                map_info=map_info,
                player_skill=PlayerSkill(
                    aim=skill.aim, speed=skill.speed, acc=skill.acc, cons=skill.cons,
                ),
                score=ScoreStats(
                    n_300=int(bounty.max_combo or 100), n_100=0, n_50=0,
                    misses=0, combo=int(bounty.max_combo or 0),
                ),
                is_first_submission=False,
                bounty_type=bounty.bounty_type,
            )
            hps_preview_hp = preview["final_hp"]

    data = {
        "bounty_id": bounty.bounty_id,
        "bounty_type": bounty.bounty_type or "First FC",
        "tier": bounty.tier or "Open",
        "title": bounty.title,
        "beatmap_title": bounty.beatmap_title,
        "beatmapset_id": bounty.beatmapset_id,
        "star_rating": bounty.star_rating,
        "drain_time": bounty.drain_time,
        "mapper_name": bounty.mapper_name,
        "deadline": dl,
        "participant_count": sub_count,
        "max_participants": bounty.max_participants,
        "conditions": _format_conditions_compact(bounty),
        "conditions_latin": _format_conditions_latin(bounty),
        "required_mods": bounty.required_mods,
        "hps_preview_hp": hps_preview_hp,
    }

    wait = await message.answer("⏳ Генерирую карточку…")
    renderer = CardRenderer()
    try:
        buf = await renderer.generate_bounty_compact_card_async(data)
    except Exception as e:
        logger.error(f"bountydetails card render failed: {e}", exc_info=True)
        await wait.edit_text(format_error("Ошибка генерации карточки."), parse_mode="HTML")
        return

    keyboard = bounty_detail_keyboard(
        message.from_user.id, bounty.bounty_id, bounty.beatmapset_id,
    )
    await wait.delete()
    await message.answer_photo(
        photo=BufferedInputFile(buf.getvalue(), filename="bounty.jpg"),
        reply_markup=keyboard,
    )


# /accept

@router.message(TextTriggerFilter("accept", "acc"))
async def accept_command(message: types.Message, trigger_args: TriggerArgs):
    args = trigger_args.args
    if not args:
        await message.answer(format_error("Использование: accept <bounty_id>"))
        return

    bounty_id = args.strip()
    telegram_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_registered_user(session, telegram_id)
        if not user:
            await message.answer(
                format_error("Вы не зарегистрированы. Используйте register [nickname]"),
                parse_mode="HTML",
            )
            return

        success, msg = await _do_accept(session, user, bounty_id)

    if success:
        await message.answer(format_success(msg), parse_mode="HTML")
    else:
        await message.answer(format_error(msg), parse_mode="HTML")
    logger.info(f"Bounty {bounty_id} accept attempt by user {telegram_id}: success={success}")


# /mybounties (/mb)

@router.message(TextTriggerFilter("mybounties", "mb"))
async def mybounties_command(message: types.Message):
    telegram_id = message.from_user.id

    async with get_db_session() as session:
        from utils.osu.resolve_user import get_registered_user
        user = await get_registered_user(session, telegram_id)
        if not user:
            await message.answer(
                format_error("Вы не зарегистрированы. Используйте register [nickname]"),
                parse_mode="HTML",
            )
            return

        subs = (await session.execute(
            select(Submission)
            .where(Submission.user_id == user.id)
            .order_by(Submission.submitted_at.desc())
            .limit(50)
        )).scalars().all()

        if not subs:
            await message.answer("У вас пока нет баунти-заявок.")
            return

        bounty_ids = {s.bounty_id for s in subs}
        bounties_map = {
            b.bounty_id: b for b in (await session.execute(
                select(Bounty).where(Bounty.bounty_id.in_(bounty_ids))
            )).scalars().all()
        }

    tracking, pending, approved, rejected = [], [], [], []
    for s in subs:
        b = bounties_map.get(s.bounty_id)
        if s.status == "tracking":
            tracking.append((s, b))
        elif s.status == "pending":
            pending.append((s, b))
        elif s.status == "approved":
            approved.append((s, b))
        else:
            rejected.append((s, b))

    from datetime import datetime as _dt
    now = _dt.utcnow()

    def _map_line(b) -> str:
        if not b:
            return "—"
        sr = f"{b.star_rating:.1f}★" if b.star_rating else "?"
        return f"{escape_html(b.title or b.bounty_id)} [{sr} · {b.bounty_type or '?'}]"

    lines = [f"<b>📋 Твои баунти — {escape_html(user.osu_username)}</b>"]

    if tracking:
        lines.append("\n⏳ <b>Отслеживается:</b>")
        for s, b in tracking:
            days = (now - s.submitted_at).days if s.submitted_at else "?"
            lines.append(f"  • {_map_line(b)} — {days}д")

    if pending:
        lines.append("\n🕐 <b>На рассмотрении:</b>")
        for s, b in pending:
            lines.append(f"  • {_map_line(b)}")

    if approved:
        lines.append(f"\n✅ <b>Одобрено ({len(approved)}):</b>")
        for s, b in approved[:7]:
            hp_str = f"+{s.hp_awarded} HP" if s.hp_awarded is not None else "—"
            dt_str = s.reviewed_at.strftime("%d.%m") if s.reviewed_at else "?"
            lines.append(f"  <code>{hp_str:>8}</code>  {_map_line(b)}  <i>({dt_str})</i>")

    if rejected:
        lines.append(f"\n❌ <b>Отклонено ({len(rejected)}):</b>")
        for s, b in rejected[:5]:
            dt_str = s.reviewed_at.strftime("%d.%m") if s.reviewed_at else "?"
            lines.append(f"  • {_map_line(b)}  <i>({dt_str})</i>")

    # Weekly claim usage summary
    async with get_db_session() as session:
        from db.models.weekly_bounty_pool import WeeklyBountyPool
        active_pool = (await session.execute(
            select(WeeklyBountyPool).where(WeeklyBountyPool.is_active == 1)
        )).scalar_one_or_none()
        if active_pool:
            weekly_claims = (await session.execute(
                select(func.count(distinct(Submission.bounty_id)))
                .join(Bounty, Bounty.bounty_id == Submission.bounty_id)
                .where(
                    Submission.user_id == user.id,
                    Bounty.week_id == active_pool.id,
                    Bounty.source == "auto",
                )
            )).scalar() or 0
            lines.append(f"\n📌 Недельных баунти принято: <b>{weekly_claims}/6</b>")

    await message.answer("\n".join(lines), parse_mode="HTML")
