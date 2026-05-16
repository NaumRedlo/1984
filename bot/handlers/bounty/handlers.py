from datetime import datetime, timezone
from aiogram import Router, types
from aiogram.types import BufferedInputFile
from sqlalchemy import select, func

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from services.image import card_renderer
from utils.hp_calculator import calculate_hps, RANK_THRESHOLDS
from utils.osu.helpers import get_community_stats
from utils.osu.resolve_user import get_registered_user
from utils.logger import get_logger
from utils.formatting.text import escape_html, format_error, format_success
from bot.filters import TextTriggerFilter, TriggerArgs

logger = get_logger(__name__)

router = Router(name="bounty")

RANK_ORDER = [r[1] for r in reversed(RANK_THRESHOLDS)]  # Candidate, Party Member, ...


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
        stmt = select(Bounty).where(Bounty.status == "active")
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
                entries.append({
                    "bounty_id": b.bounty_id,
                    "bounty_type": b.bounty_type or "First FC",
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
                    f"<b>#{escape_html(b.bounty_id)}</b> | "
                    f"{escape_html(b.title)}\n"
                    f"  {b.star_rating:.2f}★ | Дедлайн: {dl} | "
                    f"Участников: {sub_count}{max_p_str}"
                )

    try:
        buf = await card_renderer.generate_bountylist_card_async(entries)
        photo = BufferedInputFile(buf.read(), filename="bountylist.png")
        await message.answer_photo(photo=photo)
    except Exception as img_err:
        logger.warning(f"Bountylist card generation failed: {img_err}")
        await message.answer("\n".join(fallback_lines), parse_mode="HTML")


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

        has_conditions = False
        if bounty.min_accuracy is not None:
            lines.append(f"  Мин. точность: {bounty.min_accuracy}%")
            has_conditions = True
        if bounty.required_mods:
            lines.append(f"  Обязательные моды: {bounty.required_mods}")
            has_conditions = True
        if bounty.max_misses is not None:
            lines.append(f"  Макс. миссов: {bounty.max_misses}")
            has_conditions = True
        if bounty.min_rank:
            lines.append(f"  Мин. ранг: {bounty.min_rank}")
            has_conditions = True
        if bounty.min_hp is not None:
            lines.append(f"  Мин. HP: {bounty.min_hp}")
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
            community_stats = await get_community_stats(session)
            hp_result = calculate_hps(
                result_type="win",
                star_rating=bounty.star_rating,
                drain_time_seconds=bounty.drain_time,
                player_pp=user.player_pp or 0,
                community_stats=community_stats,
                accuracy=95.0,
                cs=bounty.cs or 0.0,
                od=bounty.od or 0.0,
                ar=bounty.ar or 0.0,
                hp_drain=bounty.hp_drain or 0.0,
                bpm=bounty.bpm or 0.0,
                max_combo=bounty.max_combo or 0,
            )
            hps_preview_hp = hp_result["final_hp"]
            lines.extend([
                "═" * 28,
                "<b>HPS-превью (ваш потенциал):</b>",
                f"  Победа: ~{hps_preview_hp} HP",
            ])

    fallback_text = "\n".join(lines)

    # Try PNG card, fallback to text
    try:
        conditions_list = []
        if bounty.min_accuracy is not None:
            conditions_list.append(f"Accuracy: {bounty.min_accuracy}%")
        if bounty.max_misses is not None:
            conditions_list.append(f"Misses: {bounty.max_misses}")
        if bounty.min_rank:
            conditions_list.append(f"Rank: {bounty.min_rank}")
        if bounty.min_hp is not None:
            conditions_list.append(f"Min HP: {bounty.min_hp}")

        bounty_data = {
            "bounty_id": bounty.bounty_id,
            "bounty_type": bounty.bounty_type or "First FC",
            "title": bounty.title,
            "beatmap_id": bounty.beatmap_id,
            "beatmapset_id": bounty.beatmapset_id,
            "beatmap_title": bounty.beatmap_title,
            "mapper_id": bounty.mapper_id,
            "mapper_name": bounty.mapper_name,
            "mapper_avatar_url": bounty.mapper_avatar_url,
            "required_mods": bounty.required_mods,
            "star_rating": bounty.star_rating,
            "duration": bounty.drain_time,
            "status": bounty.status,
            "conditions": conditions_list,
            "participant_count": sub_count,
            "max_participants": bounty.max_participants,
            "deadline": bounty.deadline.strftime("%d.%m.%Y %H:%M UTC") if bounty.deadline else "None",
            "hps_preview_hp": hps_preview_hp,
        }
        buf = await card_renderer.generate_bounty_card_async(bounty_data)
        photo = BufferedInputFile(buf.read(), filename="bounty.png")
        await message.answer_photo(photo=photo)
    except Exception as img_err:
        logger.warning(f"Bounty card generation failed: {img_err}")
        await message.answer(fallback_text, parse_mode="HTML")


# /submit

@router.message(TextTriggerFilter("submit"))
async def submit_command(message: types.Message, trigger_args: TriggerArgs, osu_api_client=None):
    args = trigger_args.args
    if not args:
        await message.answer(format_error("Использование: submit <bounty_id>"))
        return

    bounty_id = args.strip()
    telegram_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_registered_user(session, telegram_id)
        if not user:
            await message.answer(
                format_error("Вы не зарегистрированы. Используйте register [nickname]"),
                parse_mode="HTML"
            )
            return

        stmt = select(Bounty).where(Bounty.bounty_id == bounty_id)
        bounty = (await session.execute(stmt)).scalar_one_or_none()
        if not bounty:
            await message.answer(format_error(f"Баунти {escape_html(bounty_id)} не найден."), parse_mode="HTML")
            return

        if bounty.status != "active":
            await message.answer(format_error(f"Баунти имеет статус «{bounty.status}», приём заявок закрыт."))
            return

        now = datetime.utcnow()
        if bounty.deadline and bounty.deadline < now:
            bounty.status = "expired"
            bounty.closed_at = now
            await session.commit()
            await message.answer(format_error("Дедлайн баунти истёк."))
            return

        if bounty.max_participants:
            sub_count_stmt = select(func.count()).select_from(Submission).where(
                Submission.bounty_id == bounty_id
            )
            sub_count = (await session.execute(sub_count_stmt)).scalar() or 0
            if sub_count >= bounty.max_participants:
                await message.answer(format_error("Лимит участников достигнут."))
                return

        if bounty.min_rank:
            if not _rank_meets_minimum(user.rank, bounty.min_rank):
                await message.answer(
                    format_error(f"Ваш ранг ({user.rank}) ниже минимального ({bounty.min_rank})."),
                )
                return

        if bounty.min_hp is not None:
            if (user.hps_points or 0) < bounty.min_hp:
                await message.answer(
                    format_error(f"У вас {user.hps_points} HP, минимум для участия — {bounty.min_hp} HP."),
                )
                return

        dup_stmt = select(Submission).where(
            Submission.bounty_id == bounty_id,
            Submission.user_id == user.id,
            Submission.status.in_(("approved", "pending")),
        )
        existing = (await session.execute(dup_stmt)).scalar_one_or_none()
        if existing:
            if existing.status == "approved":
                msg = "У вас уже есть одобренная заявка на этот баунти."
            else:
                msg = f"Заявка #{existing.id} уже ждёт рассмотрения админом."
            await message.answer(format_error(msg))
            return

        submission = Submission(
            bounty_id=bounty_id,
            user_id=user.id,
            telegram_id=telegram_id,
        )

        # Fetch first score on the beatmap since bounty creation (anti-retry abuse)
        try:
            if osu_api_client and user.osu_user_id:
                scores = await osu_api_client.get_user_beatmap_scores(
                    bounty.beatmap_id, user.osu_user_id,
                    oauth_token=user.oauth_access_token,
                )
                # Filter to scores set after bounty was created, pick the earliest
                bounty_start = bounty.created_at.replace(tzinfo=timezone.utc) if bounty.created_at.tzinfo is None else bounty.created_at
                valid = []
                for s in scores:
                    ended_at = s.get("ended_at") or s.get("created_at")
                    if ended_at:
                        try:
                            from datetime import datetime as _dt
                            score_dt = _dt.fromisoformat(ended_at.replace("Z", "+00:00"))
                            if score_dt >= bounty_start:
                                valid.append((score_dt, s))
                        except Exception:
                            pass
                if valid:
                    valid.sort(key=lambda x: x[0])
                    best = valid[0][1]  # first score after bounty start
                    stats = best.get("statistics", {})
                    submission.accuracy = round(best.get("accuracy", 0) * 100, 2)
                    submission.max_combo = best.get("max_combo")
                    submission.misses = stats.get("count_miss", 0)
                    mods = best.get("mods", [])
                    if isinstance(mods, list):
                        submission.mods = ",".join(
                            m.get("acronym", m) if isinstance(m, dict) else str(m)
                            for m in mods
                        ) or None
                    elif isinstance(mods, str):
                        submission.mods = mods or None
                    submission.score_rank = best.get("rank")
        except Exception as e:
            logger.warning(f"submit: failed to fetch osu score for user {user.id}: {e}")

        session.add(submission)
        await session.commit()

        await message.answer(
            format_success(f"Заявка #{submission.id} отправлена на рассмотрение!\n"
                          f"Баунти: {escape_html(bounty_id)}"),
            parse_mode="HTML"
        )
        logger.info(f"Submission #{submission.id} by user {user.id} for bounty {bounty_id}")
