from datetime import datetime, timezone
from aiogram import Router, types
from sqlalchemy import select, func

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
from services.hps.bsk_user_skill import compute_bsk_user_skill
from services.hps.payout import _map_info_for_bounty  # internal: same logic everyone uses
from utils.logger import get_logger
from utils.formatting.text import escape_html, format_error, format_success
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.utils.paginator import build_pages, store_pages, nav_keyboard

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


# /bountylist (/bli)

@router.message(TextTriggerFilter("bountylist", "bli"))
async def bountylist_command(message: types.Message, trigger_args: TriggerArgs = None):
    now = datetime.utcnow()
    async with get_db_session() as session:
        # Skip rows whose deadline has passed but the expirer hasn't flipped yet —
        # purely a read; the bounty_expirer background task owns the status write.
        # Order: weekly tier-pool first (C, B, A, Open), manual after. Within
        # each group, newest first so freshly-generated/created bounties surface.
        stmt = (
            select(Bounty)
            .where(Bounty.status == "active")
            .order_by(
                Bounty.tier.asc().nulls_last(),
                Bounty.created_at.desc(),
            )
        )
        bounties = (await session.execute(stmt)).scalars().all()

        active = [b for b in bounties if not (b.deadline and b.deadline < now)]

        # Resolve hosts in one batch — the list card renders each row with the
        # host's avatar + nickname under the bounty ID.
        host_ids = {b.created_by for b in active}
        hosts_by_tg: dict = {}
        if host_ids:
            host_rows = (await session.execute(
                select(User).where(User.telegram_id.in_(host_ids))
            )).scalars().all()
            hosts_by_tg = {u.telegram_id: u for u in host_rows}

        entries = []
        fallback_lines = ["<b>Активные баунти:</b>", "═" * 28]

        if not active:
            fallback_lines.append("Нет активных баунти.")
        else:
            for b in active:
                sub_count_stmt = select(func.count()).select_from(Submission).where(
                    Submission.bounty_id == b.bounty_id
                )
                sub_count = (await session.execute(sub_count_stmt)).scalar() or 0

                dl = b.deadline.strftime("%d.%m %H:%M") if b.deadline else "—"
                max_p_str = f"/{b.max_participants}" if b.max_participants else ""

                host = hosts_by_tg.get(b.created_by)
                # Tier badge: auto-generated bounties carry a tier slot,
                # manual bounties get a [Manual] tag so they're visually
                # distinct in the list.
                if b.source == "auto" and b.tier:
                    tier_badge = f"[Tier {b.tier}]"
                else:
                    tier_badge = "[Manual]"

                entries.append({
                    "bounty_id": b.bounty_id,
                    "bounty_type": b.bounty_type or "First FC",
                    "tier_badge": tier_badge,
                    "tier": b.tier,
                    "source": b.source or "manual",
                    "title": b.title,
                    "beatmap_title": b.beatmap_title,
                    "beatmapset_id": b.beatmapset_id,
                    "star_rating": b.star_rating,
                    "deadline": dl,
                    "participant_count": sub_count,
                    "max_participants": b.max_participants,
                    "host_name": host.osu_username if host else None,
                    "host_avatar_url": host.avatar_url if host else None,
                })

                fallback_lines.append(
                    f"{tier_badge} <b>#{escape_html(b.bounty_id)}</b> | "
                    f"{escape_html(b.title)}\n"
                    f"  {b.star_rating:.2f}★ | Дедлайн: {dl} | "
                    f"Участников: {sub_count}{max_p_str}"
                )

    uid = message.from_user.id
    pages = build_pages(fallback_lines)
    store_pages("bli", uid, pages)
    keyboard = nav_keyboard("bli", uid, page=0, total=len(pages))
    await message.answer(pages[0], parse_mode="HTML", reply_markup=keyboard)


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
            await message.answer(format_error(f"Баунти {escape_html(bounty_id)} не найден."), parse_mode="HTML")
            return

        sub_count_stmt = select(func.count()).select_from(Submission).where(
            Submission.bounty_id == bounty.bounty_id
        )
        sub_count = (await session.execute(sub_count_stmt)).scalar() or 0

        lines = [
            f"<b>Баунти #{escape_html(bounty.bounty_id)}</b>",
            "═" * 28,
            f"<b>Тип:</b> {escape_html(bounty.bounty_type or 'First FC')}",
            f"<b>Название:</b> {escape_html(bounty.title)}",
            f"<b>Карта:</b> {escape_html(bounty.beatmap_title)}",
            f"<b>Сложность:</b> {bounty.star_rating:.2f}★",
            f"<b>Длительность:</b> {bounty.drain_time // 60}:{bounty.drain_time % 60:02d}",
            f"<b>Статус:</b> {bounty.status}",
            "═" * 28,
            "<b>Условия:</b>",
        ]

        # Parse JSON-conditions blob (Metronome max_ur, Marathon min_combo_pct,
        # any future v2 fields). Falls back silently on malformed JSON.
        json_cond: dict = {}
        if bounty.conditions:
            try:
                import json as _json
                _parsed = _json.loads(bounty.conditions)
                if isinstance(_parsed, dict):
                    json_cond = _parsed
            except Exception:
                pass

        has_conditions = False
        if bounty.min_accuracy is not None:
            lines.append(f"  🎯 Мин. точность: {bounty.min_accuracy}%")
            has_conditions = True
        if bounty.required_mods:
            lines.append(f"  🎚 Обязательные моды: {bounty.required_mods}")
            has_conditions = True
        if bounty.max_misses is not None:
            lines.append(f"  ❌ Макс. миссов: {bounty.max_misses}")
            has_conditions = True
        if "max_ur" in json_cond:
            lines.append(f"  ⏱ Макс. UR: {json_cond['max_ur']} ms")
            has_conditions = True
        if "min_combo_pct" in json_cond:
            pct = float(json_cond["min_combo_pct"]) * 100
            lines.append(f"  🔗 Мин. комбо: ≥ {pct:.0f}% от max_combo")
            has_conditions = True
        if bounty.min_rank:
            lines.append(f"  🥇 Мин. ранг: {bounty.min_rank}")
            has_conditions = True
        if bounty.min_hp is not None:
            lines.append(f"  ❤️ Мин. HP: {bounty.min_hp}")
            has_conditions = True
        if not has_conditions:
            lines.append("  Нет")

        max_p = f"/{bounty.max_participants}" if bounty.max_participants else ""
        lines.append(f"\n<b>Участников:</b> {sub_count}{max_p}")
        dl = bounty.deadline.strftime("%d.%m.%Y %H:%M UTC") if bounty.deadline else "Нет"
        lines.append(f"<b>Дедлайн:</b> {dl}")

        user = await get_registered_user(session, message.from_user.id)
        hps_preview_hp: int | None = None
        if user:
            # v2 preview: assume the player will manage a clean run (FC, low
            # misses, UR ≈ 100 ms — Ω neutral).  Real payout depends on the
            # actual score; this is just a "what could you earn?" hint.
            map_info, _ = await _map_info_for_bounty(bounty, session)
            skill = await compute_bsk_user_skill(user, session)
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
                # ur_est_override omitted → Ω=1.0 neutral until .osr parsing is wired in
            )
            hps_preview_hp = preview["final_hp"]
            lines.extend([
                "═" * 28,
                "<b>HPS-превью (ваш потенциал):</b>",
                f"  Победа (FC, UR≈100): ~{hps_preview_hp} HP",
            ])

    await message.answer("\n".join(lines), parse_mode="HTML")


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

        stmt = select(Bounty).where(Bounty.bounty_id == bounty_id)
        bounty = (await session.execute(stmt)).scalar_one_or_none()
        if not bounty:
            await message.answer(format_error(f"Баунти {escape_html(bounty_id)} не найден."), parse_mode="HTML")
            return

        if bounty.status != "active":
            await message.answer(format_error(f"Баунти имеет статус «{bounty.status}», приём закрыт."))
            return

        now = datetime.utcnow()
        if bounty.deadline and bounty.deadline < now:
            bounty.status = "expired"
            bounty.closed_at = now
            await session.commit()
            await message.answer(format_error("Дедлайн баунти истёк."))
            return

        dup_stmt = select(Submission).where(
            Submission.bounty_id == bounty_id,
            Submission.user_id == user.id,
            Submission.status.in_(("approved", "pending", "tracking")),
        )
        existing = (await session.execute(dup_stmt)).scalar_one_or_none()
        if existing:
            status_msg = {
                "approved": "У вас уже есть одобренная заявка на этот баунти.",
                "pending": f"Заявка #{existing.id} ждёт рассмотрения.",
                "tracking": "Вы уже приняли этот баунти. Скоры отслеживаются.",
            }
            await message.answer(format_error(status_msg.get(existing.status, "Дубликат.")))
            return

        submission = Submission(
            bounty_id=bounty_id,
            user_id=user.id,
            telegram_id=telegram_id,
            status="tracking",
        )
        session.add(submission)
        await session.commit()

        await message.answer(
            format_success(
                f"Ты принял баунти «{escape_html(bounty.title)}».\n"
                f"Твои скоры на этой карте отслеживаются автоматически."
            ),
            parse_mode="HTML",
        )
        logger.info(f"Bounty {bounty_id} accepted by user {user.id} (tracking #{submission.id})")
