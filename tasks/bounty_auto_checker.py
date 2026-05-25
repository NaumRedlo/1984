"""Background task: auto-check scores for users who accepted bounties."""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy import select

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from services.hps import compute_payout
from utils.hp_calculator import get_rank_for_hp
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


def _parse_conditions_json(bounty: Bounty) -> dict:
    """Decode `bounty.conditions` JSON text, returning {} on missing/invalid."""
    raw = getattr(bounty, "conditions", None)
    if not raw:
        return {}
    try:
        import json
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _check_conditions(
    score: dict,
    bounty: Bounty,
    *,
    ur_est: float | None = None,
    beatmap_max_combo: int | None = None,
) -> tuple[str, bool]:
    """Validate a score against bounty conditions.

    Reads both legacy columns (`min_accuracy`, `required_mods`, `max_misses`)
    and the JSON `conditions` blob (`max_ur`, `min_combo_pct`). Returns
    (`result_type`, `auto_approve`):
      - ("win", True): all conditions met AND no misses
      - ("condition", True): all conditions met (with misses)
      - ("pending", False): at least one condition failed → manual review

    `ur_est` must be passed if the caller has already computed it for this
    score — otherwise the `max_ur` check is silently skipped (auto-approve
    false-positives are worse than waiting for manual review).
    """
    acc = score.get("accuracy", 0) * 100
    stats = score.get("statistics", {})
    misses = stats.get("count_miss", 0)
    mods = _extract_mods(score)
    score_combo = int(score.get("max_combo") or 0)

    all_met = True

    # ── Legacy columns ──────────────────────────────────────────────────
    if bounty.min_accuracy and acc < bounty.min_accuracy:
        all_met = False
    if bounty.max_misses is not None and misses > bounty.max_misses:
        all_met = False
    if bounty.required_mods:
        req = {m.strip().upper() for m in bounty.required_mods.replace(",", " ").split() if m.strip()}
        if not req.issubset(mods):
            all_met = False

    # ── JSON conditions (Marathon / Metronome) ──────────────────────────
    cond = _parse_conditions_json(bounty)

    # max_ur: Metronome bounties (UR ≤ N ms). Requires the caller to provide
    # ur_est; if absent, skip the check (auto-approve waits for the explicit
    # value rather than guessing). UR=None means "unknown" (e.g. no hits).
    max_ur = cond.get("max_ur")
    if max_ur is not None:
        if ur_est is None or ur_est > float(max_ur):
            all_met = False

    # min_combo_pct: Marathon bounties (combo ≥ pct × beatmap.max_combo).
    # The reference max_combo is passed in by the caller (looked up via
    # osu! API). Falls back to bounty.max_combo if any (legacy/manual);
    # if neither is available, auto-approve fails (safe default).
    min_combo_pct = cond.get("min_combo_pct")
    if min_combo_pct is not None:
        ref_combo = int(beatmap_max_combo or getattr(bounty, "max_combo", 0) or 0)
        if ref_combo > 0 and score_combo > 0:
            achieved_pct = score_combo / ref_combo
            if achieved_pct < float(min_combo_pct):
                all_met = False
        else:
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

            _stats_pre = first_score.get("statistics", {})
            _n300 = int(_stats_pre.get("count_300") or _stats_pre.get("great") or 0)
            _n100 = int(_stats_pre.get("count_100") or _stats_pre.get("ok") or 0)
            _n50  = int(_stats_pre.get("count_50")  or _stats_pre.get("meh") or 0)
            _mods_str_val = _mods_str(first_score)
            # UR is populated only from replay parsing (.osr); until then
            # ur_est=None causes Metronome (max_ur) bounties to fall through
            # to manual review — the safe default.
            ur_est_val = None

            # Marathon bounties need beatmap.max_combo for the combo% check.
            # Cheap call once per bounty hit; only invoked when a score is
            # actually being evaluated (not on every poll cycle).
            beatmap_max_combo: int | None = None
            _cond_json = _parse_conditions_json(bounty)
            if _cond_json.get("min_combo_pct") is not None:
                try:
                    bm = await osu_api_client.get_beatmap(bounty.beatmap_id)
                    if bm:
                        beatmap_max_combo = int(bm.get("max_combo") or 0)
                except Exception as e:
                    logger.warning(
                        f"bounty_auto_checker: get_beatmap failed for "
                        f"bounty={bounty.bounty_id} bm={bounty.beatmap_id}: {e}"
                    )

            result_type, auto_approve = _check_conditions(
                first_score, bounty,
                ur_est=ur_est_val,
                beatmap_max_combo=beatmap_max_combo,
            )

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
                sub_fresh.mods = _mods_str_val
                sub_fresh.score_rank = first_score.get("rank")
                # v2 needs the raw hit counts for UR; computed above already.
                sub_fresh.n_300 = _n300
                sub_fresh.n_100 = _n100
                sub_fresh.n_50  = _n50
                sub_fresh.ur_est = ur_est_val

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
                        u.rank = get_rank_for_hp(u.hps_points)
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
