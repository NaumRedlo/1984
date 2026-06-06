"""Background task: auto-check scores for users who accepted bounties."""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy import select, update

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from services.hps import compute_payout
from services.bounty.notify import send_bounty_event
from utils.hp_calculator import get_rank_for_hp
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.osu.replay_parser import parse_ur_from_osr

logger = get_logger("tasks.bounty_auto_checker")

CHECK_INTERVAL = 300


# Mods that do NOT alter map difficulty / scoring in a way that affects the
# bounty challenge. They're stripped before required-mods comparison so an
# HD bounty still passes when the player additionally has NF / SD / PF / CL
# active (those don't make the map easier or harder).
HARMLESS_MODS: frozenset[str] = frozenset({"NF", "SD", "PF", "CL"})


def _extract_mods(score: dict) -> set[str]:
    """Return the set of difficulty-relevant mods played on this score.

    Harmless mods (NF, SD, PF, CL) are stripped — they don't alter the
    map's challenge so they don't participate in required-mods matching.
    """
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
    return result - HARMLESS_MODS


def _parse_required_mods(raw: str | None) -> set[str]:
    """Parse Bounty.required_mods into a normalised set.

    Empty/None → empty set (NM bounty: player must use no difficulty-altering
    mods). Harmless mods (NF/SD/PF/CL) are stripped so the bounty author can't
    accidentally require them.
    """
    if not raw:
        return set()
    parts = {m.strip().upper() for m in raw.replace(",", " ").split() if m.strip()}
    return parts - HARMLESS_MODS


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
      - ("ur_needed", False): every non-UR condition passes but `max_ur` is
        set and `ur_est` wasn't supplied → caller must fetch the replay,
        compute UR, and re-call with `ur_est=<float>`.
      - ("pending", False): at least one condition failed → manual review

    `ur_est` must be passed if the caller has already computed it for this
    score; otherwise the `max_ur` condition is left unresolved (the
    auto-checker uses this as the trigger to download a replay).
    """
    acc = score.get("accuracy", 0) * 100
    stats = score.get("statistics", {})
    misses = stats.get("count_miss", 0)
    mods = _extract_mods(score)
    score_combo = int(score.get("max_combo") or 0)

    # Hard gate: failed/dropped plays must NEVER auto-approve. osu!'s recent
    # endpoint is queried with include_fails=1 (we want to track attempts for
    # logs), so this filter is what stops players who quit mid-map from
    # collecting HP. `passed` is the canonical flag from the API; `rank=="F"`
    # is the belt-and-braces check for older response shapes.
    if not score.get("passed", True):
        return "pending", False
    if (score.get("rank") or "").upper() == "F":
        return "pending", False

    all_met = True
    ur_unresolved = False

    # ── Legacy columns ──────────────────────────────────────────────────
    if bounty.min_accuracy is not None and acc < bounty.min_accuracy:
        all_met = False
    if bounty.max_misses is not None and misses > bounty.max_misses:
        all_met = False
    # Strict equality on difficulty-relevant mods. Both sides are normalised
    # via _extract_mods (player) / _parse_required_mods (bounty) — harmless
    # mods like NF/SD/PF/CL are already stripped from both.
    req = _parse_required_mods(bounty.required_mods)
    if mods != req:
        all_met = False

    # ── JSON conditions (Marathon / Metronome) ──────────────────────────
    cond = _parse_conditions_json(bounty)

    # max_ur: Metronome bounties (UR ≤ N ms). When `ur_est` isn't supplied
    # we don't fail the check — we flag it as unresolved so the auto-checker
    # can pull the replay and re-run with a real UR. A second call with an
    # explicit `ur_est` either passes or fails the condition normally.
    max_ur = cond.get("max_ur")
    if max_ur is not None:
        if ur_est is None:
            ur_unresolved = True
        elif ur_est > float(max_ur):
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

    if all_met and ur_unresolved:
        return "ur_needed", False
    if all_met and misses == 0:
        return "win", True
    elif all_met:
        return "condition", True
    else:
        return "pending", False


def _has_extra_challenge(mods: set[str]) -> bool:
    return "HD" in mods and "HR" in mods


async def _resolve_ur_for_score(
    score: dict,
    bounty: Bounty,
    osu_api_client,
    osu_text_cache: dict[int, str | None],
) -> float | None:
    """Download the replay for `score` and parse its UR.

    Returns the UR in ms or None when the replay isn't available / can't
    be parsed (corrupt file, wrong mode, too few matched hits, etc.).

    `osu_text_cache` is shared across the batch so we hit the .osu CDN
    at most once per beatmap. A cached `None` means "tried and failed";
    we don't retry within this cycle.
    """
    score_id = score.get("id")
    if not score_id:
        return None

    try:
        osr_bytes = await osu_api_client.download_replay(int(score_id))
    except Exception as e:
        logger.warning(f"auto_checker: download_replay({score_id}) failed: {e}")
        return None
    if not osr_bytes:
        return None

    bm_id = int(bounty.beatmap_id)
    if bm_id not in osu_text_cache:
        try:
            raw = await osu_api_client.download_osu_file(bm_id)
            osu_text_cache[bm_id] = raw.decode("utf-8", errors="replace") if raw else None
        except Exception as e:
            logger.warning(f"auto_checker: download_osu_file({bm_id}) failed: {e}")
            osu_text_cache[bm_id] = None
    osu_text = osu_text_cache[bm_id]
    if not osu_text:
        return None

    try:
        return await parse_ur_from_osr(osr_bytes, osu_text=osu_text, od=bounty.od)
    except Exception as e:
        logger.warning(f"auto_checker: parse_ur_from_osr failed: {e}")
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

    # Close tracking submissions whose bounty is no longer active.
    stale_ids = [
        s.id for s in tracking_subs
        if bounty_map.get(s.bounty_id) is None
        or bounty_map[s.bounty_id].status != "active"
    ]
    if stale_ids:
        async with get_db_session() as session:
            await session.execute(
                update(Submission)
                .where(Submission.id.in_(stale_ids), Submission.status == "tracking")
                .values(status="expired")
            )
            await session.commit()
        logger.info(f"auto_checker: expired {len(stale_ids)} stale tracking submission(s)")
        tracking_subs = [s for s in tracking_subs if s.id not in set(stale_ids)]
        if not tracking_subs:
            return 0

    by_user: dict[int, list[Submission]] = defaultdict(list)
    for s in tracking_subs:
        by_user[s.user_id].append(s)

    # Shared across all submissions in this batch so we don't re-download the
    # same .osu file for multiple players doing the same bounty.
    osu_text_cache: dict[int, str | None] = {}

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

            # Marathon bounties need beatmap.max_combo for the combo% check.
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

            # Find the first chronological score that satisfies all conditions.
            # Scores that don't qualify are ignored — the submission stays in
            # tracking so the player can keep trying until the deadline.
            # For Metronome (`max_ur`) bounties the first pass returns
            # "ur_needed"; we then pull the replay, recompute UR, and re-check.
            qualifying_score = None
            qualifying_result = None
            qualifying_ur: float | None = None
            for _, score in valid_scores:
                result_type, auto_approve = _check_conditions(
                    score, bounty, ur_est=None, beatmap_max_combo=beatmap_max_combo,
                )
                if auto_approve:
                    qualifying_score = score
                    qualifying_result = result_type
                    break
                if result_type != "ur_needed":
                    continue
                ur = await _resolve_ur_for_score(
                    score, bounty, osu_api_client, osu_text_cache,
                )
                if ur is None:
                    # Replay unavailable / unparseable — leave tracking, retry
                    # next cycle (the player may upload it later or re-submit).
                    continue
                result_type, auto_approve = _check_conditions(
                    score, bounty, ur_est=ur, beatmap_max_combo=beatmap_max_combo,
                )
                if auto_approve:
                    qualifying_score = score
                    qualifying_result = result_type
                    qualifying_ur = ur
                    break

            if qualifying_score is None:
                # No qualifying score yet — leave tracking, nothing to commit.
                continue

            _stats_pre = qualifying_score.get("statistics", {})
            _n300 = int(_stats_pre.get("count_300") or _stats_pre.get("great") or 0)
            _n100 = int(_stats_pre.get("count_100") or _stats_pre.get("ok") or 0)
            _n50  = int(_stats_pre.get("count_50")  or _stats_pre.get("meh") or 0)

            async with get_db_session() as session:
                sub_fresh = (await session.execute(
                    select(Submission).where(Submission.id == sub.id)
                )).scalar_one_or_none()
                if not sub_fresh or sub_fresh.status != "tracking":
                    continue

                # Double-payout backstop: if this user already has an approved
                # submission for this bounty (e.g. a duplicate tracking row that
                # slipped in before the unique index existed, or a replay-upload
                # approval racing this cycle), void this row instead of paying
                # the same bounty twice.
                dup_approved = (await session.execute(
                    select(Submission.id).where(
                        Submission.bounty_id == sub.bounty_id,
                        Submission.user_id == sub_fresh.user_id,
                        Submission.status == "approved",
                        Submission.id != sub_fresh.id,
                    )
                )).first()
                if dup_approved:
                    sub_fresh.status = "expired"
                    await session.commit()
                    continue

                stats = qualifying_score.get("statistics", {})
                sub_fresh.accuracy    = round(qualifying_score.get("accuracy", 0) * 100, 2)
                sub_fresh.max_combo   = qualifying_score.get("max_combo")
                sub_fresh.misses      = stats.get("count_miss", 0)
                sub_fresh.mods        = _mods_str(qualifying_score)
                sub_fresh.score_rank  = qualifying_score.get("rank")
                sub_fresh.n_300       = _n300
                sub_fresh.n_100       = _n100
                sub_fresh.n_50        = _n50
                sub_fresh.ur_est      = qualifying_ur
                sub_fresh.status      = "approved"
                sub_fresh.result_type = qualifying_result
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
                    result_type=qualifying_result,
                    is_first_submission=is_first,
                    bounty_type=bounty.bounty_type,
                )

                hp_awarded = hp_result["final_hp"]
                sub_fresh.hp_awarded = hp_awarded

                if u:
                    # Atomic increment so two concurrent award flows can't
                    # clobber each other's HP (lost update — audit #7).
                    await session.execute(
                        update(User).where(User.id == uid).values(
                            hps_points=User.hps_points + hp_awarded,
                            bounties_participated=User.bounties_participated + 1,
                        ).execution_options(synchronize_session=False)
                    )
                    # Anchor for B(t) bootstrap multiplier: set once on first approval.
                    if u.first_approved_at is None:
                        await session.execute(
                            update(User)
                            .where(User.id == uid, User.first_approved_at.is_(None))
                            .values(first_approved_at=sub_fresh.reviewed_at or datetime.utcnow())
                            .execution_options(synchronize_session=False)
                        )
                    await session.commit()
                    # Re-read the authoritative total for rank + the notify card.
                    await session.refresh(u)
                    u.rank = get_rank_for_hp(u.hps_points or 0)
                    await session.commit()
                else:
                    await session.commit()

                if bot and u:
                    await send_bounty_event(
                        bot,
                        chat_id=u.chat_id,
                        username=u.osu_username or f"id:{u.id}",
                        bounty_title=bounty.title or bounty.bounty_id,
                        bounty_type=bounty.bounty_type,
                        tier=bounty.tier,
                        star_rating=bounty.star_rating,
                        hp_awarded=hp_awarded,
                        result_type=qualifying_result,
                        is_first=is_first,
                        old_hps=(u.hps_points or 0) - hp_awarded,
                        new_hps=u.hps_points or 0,
                    )

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
