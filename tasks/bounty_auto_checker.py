"""Background task: auto-check scores for users who accepted bounties."""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy import select

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from services.hps import compute_payout
from utils.hp_calculator import get_rank_for_hp_v2
from utils.osu.ur_estimator import estimate_ur
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger("tasks.bounty_auto_checker")

CHECK_INTERVAL = 300


def _extract_mods(score: dict) -> set[str]:
    mods = score.get("mods", [])
    result = set()
    if isinstance(mods, list):
        for m in mods:
            if isinstance(m, dict):
                result.add(m.get("acronym", "").upper())
            elif isinstance(m, str):
                result.add(m.upper())
    elif isinstance(mods, str):
        result = {m.strip().upper() for m in mods.replace(",", " ").split() if m.strip()}
    result.discard("CL")
    return result


def _mods_str(score: dict) -> str | None:
    mods = score.get("mods", [])
    if isinstance(mods, list):
        parts = [m.get("acronym", m) if isinstance(m, dict) else str(m) for m in mods]
        parts = [p for p in parts if p.upper() != "CL"]
        return ",".join(parts) or None
    return mods or None


def _check_conditions(score: dict, bounty: Bounty) -> tuple[str, bool]:
    acc = score.get("accuracy", 0) * 100
    stats = score.get("statistics", {})
    misses = stats.get("count_miss", 0)
    mods = _extract_mods(score)

    all_met = True

    if bounty.min_accuracy and acc < bounty.min_accuracy:
        all_met = False
    if bounty.max_misses is not None and misses > bounty.max_misses:
        all_met = False
    if bounty.required_mods:
        req = {m.strip().upper() for m in bounty.required_mods.replace(",", " ").split() if m.strip()}
        if not req.issubset(mods):
            all_met = False

    if all_met and misses == 0:
        return "win", True
    elif all_met:
        return "condition", True
    else:
        return "pending", False


def _has_extra_challenge(mods: set[str]) -> bool:
    return "HD" in mods and "HR" in mods


async def _get_notify_chat_id() -> int | None:
    from db.models.bot_settings import BotSettings
    async with get_db_session() as session:
        row = (await session.execute(
            select(BotSettings).where(BotSettings.key == "weekly_chat_id")
        )).scalar_one_or_none()
        if row and row.value:
            try:
                return int(row.value)
            except ValueError:
                return None
    return None


async def _check_once(bot: Bot, osu_api_client) -> int:
    processed = 0

    async with get_db_session() as session:
        tracking_subs = (await session.execute(
            select(Submission).where(Submission.status == "tracking")
        )).scalars().all()

        if not tracking_subs:
            return 0

        bounty_ids = list({s.bounty_id for s in tracking_subs})
        bounties = (await session.execute(
            select(Bounty).where(Bounty.bounty_id.in_(bounty_ids))
        )).scalars().all()
        bounty_map = {b.bounty_id: b for b in bounties}

        user_ids = list({s.user_id for s in tracking_subs})
        users = (await session.execute(
            select(User).where(User.id.in_(user_ids))
        )).scalars().all()
        user_map = {u.id: u for u in users}

    by_user: dict[int, list[Submission]] = defaultdict(list)
    for s in tracking_subs:
        by_user[s.user_id].append(s)

    notify_chat = await _get_notify_chat_id()

    for uid, subs in by_user.items():
        user = user_map.get(uid)
        if not user or not user.osu_user_id:
            continue

        try:
            recent = await osu_api_client.get_user_recent_scores(
                user.osu_user_id, limit=50, oauth_token=user.oauth_access_token,
            )
        except Exception as e:
            logger.warning(f"auto_checker: failed to fetch recent for user {uid}: {e}")
            continue

        if not recent:
            continue

        beatmap_to_scores: dict[int, list[dict]] = defaultdict(list)
        for score in recent:
            bm = score.get("beatmap", {})
            bm_id = bm.get("id") or score.get("beatmap_id")
            if bm_id:
                beatmap_to_scores[int(bm_id)].append(score)

        for sub in subs:
            bounty = bounty_map.get(sub.bounty_id)
            if not bounty or bounty.status != "active":
                continue

            scores_on_map = beatmap_to_scores.get(bounty.beatmap_id, [])
            if not scores_on_map:
                continue

            bounty_start = bounty.created_at.replace(tzinfo=timezone.utc) if bounty.created_at.tzinfo is None else bounty.created_at

            valid_scores = []
            for score in scores_on_map:
                ended_at = score.get("ended_at") or score.get("created_at")
                if not ended_at:
                    continue
                try:
                    score_dt = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                    if score_dt >= bounty_start:
                        valid_scores.append((score_dt, score))
                except Exception:
                    pass

            if not valid_scores:
                continue

            valid_scores.sort(key=lambda x: x[0])
            first_score = valid_scores[0][1]

            result_type, auto_approve = _check_conditions(first_score, bounty)

            async with get_db_session() as session:
                sub_fresh = (await session.execute(
                    select(Submission).where(Submission.id == sub.id)
                )).scalar_one_or_none()
                if not sub_fresh or sub_fresh.status != "tracking":
                    continue

                stats = first_score.get("statistics", {})
                sub_fresh.accuracy = round(first_score.get("accuracy", 0) * 100, 2)
                sub_fresh.max_combo = first_score.get("max_combo")
                sub_fresh.misses = stats.get("count_miss", 0)
                sub_fresh.mods = _mods_str(first_score)
                sub_fresh.score_rank = first_score.get("rank")
                # v2 needs the raw hit counts for UR; compute once here so the
                # downstream payout doesn't have to re-derive.
                sub_fresh.n_300 = int(stats.get("count_300") or stats.get("great") or 0)
                sub_fresh.n_100 = int(stats.get("count_100") or stats.get("ok") or 0)
                sub_fresh.n_50  = int(stats.get("count_50")  or stats.get("meh") or 0)
                sub_fresh.ur_est = estimate_ur(
                    sub_fresh.n_300, sub_fresh.n_100, sub_fresh.n_50,
                    od=float(bounty.od or 0.0),
                    mods=sub_fresh.mods,
                )

                if auto_approve:
                    sub_fresh.status = "approved"
                    sub_fresh.result_type = result_type
                    sub_fresh.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)

                    u = (await session.execute(
                        select(User).where(User.id == uid)
                    )).scalar_one_or_none()

                    is_first = (await session.execute(
                        select(Submission).where(
                            Submission.bounty_id == sub.bounty_id,
                            Submission.status == "approved",
                            Submission.id != sub_fresh.id,
                        )
                    )).first() is None

                    hp_result = await compute_payout(
                        session=session,
                        user=u,
                        bounty=bounty,
                        submission=sub_fresh,
                        result_type=result_type,
                        is_first_submission=is_first,
                    )

                    hp_awarded = hp_result["final_hp"]
                    sub_fresh.hp_awarded = hp_awarded

                    if u:
                        u.hps_points = (u.hps_points or 0) + hp_awarded
                        u.rank = get_rank_for_hp_v2(u.hps_points)
                        u.bounties_participated = (u.bounties_participated or 0) + 1

                    await session.commit()

                    if notify_chat and bot:
                        try:
                            result_names = {"win": "Победа", "condition": "Условие"}
                            await bot.send_message(
                                notify_chat,
                                f"🎯 <b>{escape_html(user.osu_username)}</b> выполнил баунти "
                                f"«{escape_html(bounty.title)}»! "
                                f"+{hp_awarded} HP ({result_names.get(result_type, result_type)})",
                                parse_mode="HTML",
                            )
                        except Exception as e:
                            logger.warning(f"auto_checker: notify failed: {e}")
                else:
                    sub_fresh.status = "pending"
                    await session.commit()

                    if notify_chat and bot:
                        try:
                            await bot.send_message(
                                notify_chat,
                                f"📋 <b>{escape_html(user.osu_username)}</b> сыграл карту баунти "
                                f"«{escape_html(bounty.title)}» — требует ревью "
                                f"(<code>rsl {sub_fresh.id}</code>)",
                                parse_mode="HTML",
                            )
                        except Exception as e:
                            logger.warning(f"auto_checker: notify failed: {e}")

                processed += 1

    return processed


async def bounty_auto_checker_loop(bot: Bot, osu_api_client, shutdown_event: asyncio.Event) -> None:
    await asyncio.sleep(30)
    while not shutdown_event.is_set():
        try:
            count = await _check_once(bot, osu_api_client)
            if count > 0:
                logger.info(f"bounty_auto_checker: processed {count} submission(s)")
        except Exception as e:
            logger.error(f"bounty_auto_checker: {e}", exc_info=True)
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=CHECK_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass
