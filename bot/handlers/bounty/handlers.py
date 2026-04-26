from datetime import datetime
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
        stmt = select(Bounty).where(Bounty.status == "active")
        bounties = (await session.execute(stmt)).scalars().all()

        active = []
        for b in bounties:
            if b.deadline and b.deadline < now:
                b.status = "expired"
                b.closed_at = now
            else:
                active.append(b)
        await session.commit()

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

                entries.append({
                    "bounty_id": b.bounty_id,
                    "title": b.title,
                    "star_rating": b.star_rating,
                    "deadline": dl,
                    "participant_count": sub_count,
                    "max_participants": b.max_participants,
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
            lines.extend([
                "═" * 28,
                "<b>HPS-превью (ваш потенциал):</b>",
                f"  Победа: ~{hp_result['final_hp']} HP",
            ])

    fallback_text = "\n".join(lines)

    # Try PNG card, fallback to text
    try:
        conditions_list = []
        if bounty.min_accuracy is not None:
            conditions_list.append(f"Min accuracy: {bounty.min_accuracy}%")
        if bounty.required_mods:
            conditions_list.append(f"Required mods: {bounty.required_mods}")
        if bounty.max_misses is not None:
            conditions_list.append(f"Max misses: {bounty.max_misses}")
        if bounty.min_rank:
            conditions_list.append(f"Min rank: {bounty.min_rank}")
        if bounty.min_hp is not None:
            conditions_list.append(f"Min HP: {bounty.min_hp}")

        bounty_data = {
            "bounty_id": bounty.bounty_id,
            "bounty_type": bounty.bounty_type or "First FC",
            "title": bounty.title,
            "beatmap_title": bounty.beatmap_title,
            "star_rating": bounty.star_rating,
            "duration": bounty.drain_time,
            "status": bounty.status,
            "conditions": conditions_list,
            "participant_count": sub_count,
            "max_participants": bounty.max_participants,
            "deadline": bounty.deadline.strftime("%d.%m.%Y %H:%M UTC") if bounty.deadline else "None",
            "hps_preview_hp": hp_result["final_hp"] if user else None,
        }
        buf = await card_renderer.generate_bounty_card_async(bounty_data)
        photo = BufferedInputFile(buf.read(), filename="bounty.png")
        await message.answer_photo(photo=photo)
    except Exception as img_err:
        logger.warning(f"Bounty card generation failed: {img_err}")
        await message.answer(fallback_text, parse_mode="HTML")


# /submit

@router.message(TextTriggerFilter("submit"))
async def submit_command(message: types.Message, trigger_args: TriggerArgs):
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
            Submission.status == "approved"
        )
        existing = (await session.execute(dup_stmt)).scalar_one_or_none()
        if existing:
            await message.answer(format_error("У вас уже есть одобренная заявка на этот баунти."))
            return

        submission = Submission(
            bounty_id=bounty_id,
            user_id=user.id,
            telegram_id=telegram_id,
        )
        session.add(submission)
        await session.commit()

        await message.answer(
            format_success(f"Заявка #{submission.id} отправлена на рассмотрение!\n"
                          f"Баунти: {escape_html(bounty_id)}"),
            parse_mode="HTML"
        )
        logger.info(f"Submission #{submission.id} by user {user.id} for bounty {bounty_id}")
