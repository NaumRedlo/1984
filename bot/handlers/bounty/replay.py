"""Manual `.osr` upload fallback for Metronome bounties.

When the auto-checker can't pull a replay via the osu! API (`has_replay`
is false, the score rolled off the recent-50 window, etc.) the player can
upload the local `.osr` straight to the bot. This handler:

  1. Catches any document whose filename ends in `.osr`.
  2. Matches the replay's `beatmap_hash` against the player's active
     tracking submissions on bounties with a `max_ur` condition.
  3. Parses UR via `utils.osu.replay_parser.parse_ur_from_osr` and pulls
     score stats (acc / misses / mods / combo) straight out of the .osr
     header — no second API round-trip needed.
  4. Re-uses `_check_conditions` to validate, then mirrors the
     auto-checker's approve / award flow.

Score stats in a .osr file are player-trusted (the client wrote them).
That's fine for an opt-in upload — the player is identifying themselves
by being logged in. If forgery becomes an issue we can cross-check
against the osu! API by looking the score up by replay timestamp.
"""

import hashlib
import json as _json
import os
import tempfile
from datetime import datetime, timezone
from utils.timeutils import utcnow
from typing import Optional

from aiogram import F, Router, types
from osrparse import Replay
from osrparse.utils import GameMode, Mod
from sqlalchemy import select, update

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from services.hps import compute_payout
from tasks.bounty_auto_checker import _check_conditions
from utils.formatting.text import escape_html, format_error, format_success
from utils.hp_calculator import get_rank_for_hp
from utils.logger import get_logger
from utils.osu.replay_parser import parse_ur_from_osr
from utils.osu.resolve_user import get_registered_user

logger = get_logger(__name__)

router = Router(name="bounty-replay")


# osrparse Mod (long names) → osu! acronyms understood by _extract_mods /
# _parse_required_mods in the auto-checker. We only enumerate mods that
# actually affect difficulty or the harmless-mod strip-list — TouchDevice,
# Autoplay, Cinema and the mania keys aren't relevant here.
_MOD_TO_ACRONYM: dict[Mod, str] = {
    Mod.NoFail:      "NF",
    Mod.Easy:        "EZ",
    Mod.Hidden:      "HD",
    Mod.HardRock:    "HR",
    Mod.SuddenDeath: "SD",
    Mod.DoubleTime:  "DT",
    Mod.HalfTime:    "HT",
    Mod.Nightcore:   "NC",
    Mod.Flashlight:  "FL",
    Mod.SpunOut:     "SO",
    Mod.Perfect:     "PF",
    Mod.Relax:       "RX",
    Mod.Autopilot:   "AP",
}


def _replay_mods(mods_flag: Mod) -> list[str]:
    """Decompose a Mod bitflag into a sorted list of acronyms."""
    return sorted(acro for m, acro in _MOD_TO_ACRONYM.items() if mods_flag & m)


def _replay_to_score_dict(replay: Replay) -> dict:
    """Build a dict shaped like an osu! API score row for `_check_conditions`."""
    total_hits = replay.count_300 + replay.count_100 + replay.count_50 + replay.count_miss
    if total_hits == 0:
        accuracy = 0.0
    else:
        accuracy = (
            (300 * replay.count_300 + 100 * replay.count_100 + 50 * replay.count_50)
            / (300 * total_hits)
        )
    return {
        "accuracy": accuracy,
        "statistics": {
            "count_300": replay.count_300,
            "count_100": replay.count_100,
            "count_50":  replay.count_50,
            "count_miss": replay.count_miss,
        },
        "mods":      _replay_mods(replay.mods),
        "max_combo": replay.max_combo,
    }


async def _load_candidates(user_id: int) -> list[tuple[Submission, Bounty]]:
    """Return tracking submissions for this user whose bounty has `max_ur`."""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(Submission, Bounty)
            .join(Bounty, Bounty.bounty_id == Submission.bounty_id)
            .where(
                Submission.user_id == user_id,
                Submission.status == "tracking",
                Bounty.status == "active",
            )
        )).all()
    out: list[tuple[Submission, Bounty]] = []
    for sub, bounty in rows:
        raw = bounty.conditions or ""
        if not raw:
            continue
        try:
            data = _json.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("max_ur") is not None:
            out.append((sub, bounty))
    return out


async def _match_replay_to_bounty(
    replay: Replay,
    candidates: list[tuple[Submission, Bounty]],
    osu_api_client,
) -> Optional[tuple[Submission, Bounty, bytes]]:
    """Find the candidate whose .osu md5 matches `replay.beatmap_hash`."""
    cache: dict[int, bytes | None] = {}
    for sub, bounty in candidates:
        bm_id = int(bounty.beatmap_id)
        if bm_id not in cache:
            try:
                cache[bm_id] = await osu_api_client.download_osu_file(bm_id)
            except Exception as e:
                logger.warning(f"replay upload: download_osu_file({bm_id}) failed: {e}")
                cache[bm_id] = None
        raw = cache[bm_id]
        if not raw:
            continue
        if hashlib.md5(raw).hexdigest() == replay.beatmap_hash:
            return sub, bounty, raw
    return None


def _fingerprint_matches(replay: Replay, score: dict) -> bool:
    """True if an osu! API score's hit counts + max combo match the replay.

    Tolerates both the lazer (`great`/`ok`/`meh`/`miss`) and legacy
    (`count_300`/...) statistics key shapes.
    """
    st = score.get("statistics") or {}

    def g(*keys: str) -> int:
        for k in keys:
            v = st.get(k)
            if v is not None:
                return int(v)
        return 0

    return (
        g("count_300", "great") == replay.count_300
        and g("count_100", "ok") == replay.count_100
        and g("count_50", "meh") == replay.count_50
        and g("count_miss", "miss") == replay.count_miss
        and int(score.get("max_combo") or 0) == int(replay.max_combo or 0)
    )


async def _find_matching_real_score(
    replay: Replay, bounty: Bounty, osu_user_id: int, osu_api_client,
    oauth_token: Optional[str],
) -> Optional[dict]:
    """Return the user's real osu! score on this beatmap whose hit-count
    fingerprint matches the uploaded replay, or None if none does.

    The .osr header stats are client-written (forgeable), so a fabricated play
    that was never submitted online has no matching server score — this is the
    anti-forgery gate before any HP is credited.
    """
    try:
        real = await osu_api_client.get_user_beatmap_scores(
            int(bounty.beatmap_id), int(osu_user_id), oauth_token=oauth_token,
        )
    except Exception as e:
        logger.warning(f"replay upload: get_user_beatmap_scores failed: {e}")
        return None
    for s in real or []:
        if _fingerprint_matches(replay, s):
            return s
    return None


@router.message(F.document)
async def handle_replay_upload(message: types.Message, osu_api_client=None, tenant_chat_id=None) -> None:
    doc = message.document
    name = (doc.file_name or "").lower()
    if not name.endswith(".osr"):
        return
    if osu_api_client is None:
        return

    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
    if not user:
        return  # silent — random .osr from unregistered user, not our business

    candidates = await _load_candidates(user.id)
    if not candidates:
        return  # nothing tracking that needs UR — don't spam

    tmp_fd, osr_path = tempfile.mkstemp(suffix=".osr")
    os.close(tmp_fd)
    try:
        await message.bot.download(doc, destination=osr_path)
        with open(osr_path, "rb") as f:
            osr_bytes = f.read()
    finally:
        try:
            os.unlink(osr_path)
        except OSError:
            pass

    try:
        replay = Replay.from_string(osr_bytes)
    except Exception as e:
        logger.info(f"replay upload: osrparse failed for tg={tg_id}: {e}")
        await message.reply(format_error("Не удалось прочитать .osr."), parse_mode="HTML")
        return

    if replay.mode != GameMode.STD:
        await message.reply(
            format_error("Поддерживаются только реплеи osu!std."),
            parse_mode="HTML",
        )
        return

    matched = await _match_replay_to_bounty(replay, candidates, osu_api_client)
    if matched is None:
        await message.reply(
            format_error("Реплей не подходит ни к одному вашему активному баунти."),
            parse_mode="HTML",
        )
        return

    sub, bounty, osu_raw = matched
    osu_text = osu_raw.decode("utf-8", errors="replace")

    bounty_start = (
        bounty.created_at.replace(tzinfo=timezone.utc)
        if bounty.created_at.tzinfo is None else bounty.created_at
    )
    if replay.timestamp < bounty_start:
        await message.reply(
            format_error("Реплей записан до создания баунти — не засчитан."),
            parse_mode="HTML",
        )
        return

    # Anti-forgery: .osr stats are client-written, so confirm this exact play
    # was actually submitted to osu! before crediting. Match the replay's
    # hit-count fingerprint against the user's real scores on this beatmap.
    if not user.osu_user_id:
        await message.reply(
            format_error("Не привязан osu!-аккаунт — не могу сверить скор."),
            parse_mode="HTML",
        )
        return
    real_score = await _find_matching_real_score(
        replay, bounty, user.osu_user_id, osu_api_client, user.oauth_access_token,
    )
    if not real_score:
        await message.reply(
            format_error(
                "Не нашёл этот скор на серверах osu! — убедись, что он засабмичен "
                "онлайн (реплей должен соответствовать реальному скору)."
            ),
            parse_mode="HTML",
        )
        return
    # When the matched real score still has a downloadable replay, parse UR from
    # the server's authoritative frames so even the timing can't be doctored
    # locally. Otherwise fall back to the uploaded .osr (the play is already
    # verified real by the fingerprint match above).
    if real_score.get("replay") and real_score.get("id"):
        try:
            server_osr = await osu_api_client.download_replay(int(real_score["id"]))
            if server_osr:
                osr_bytes = server_osr
        except Exception:
            logger.debug("replay upload: server replay download failed", exc_info=True)

    ur = await parse_ur_from_osr(osr_bytes, osu_text=osu_text, od=bounty.od)
    if ur is None:
        await message.reply(
            format_error("Не удалось рассчитать UR из реплея."),
            parse_mode="HTML",
        )
        return

    score_dict = _replay_to_score_dict(replay)
    result_type, auto_approve = _check_conditions(
        score_dict, bounty, ur_est=ur, beatmap_max_combo=None,
    )

    if not auto_approve:
        await message.reply(
            format_error(
                f"Реплей не подходит для «{escape_html(bounty.title)}»: "
                f"UR={ur:.1f} мс, acc={score_dict['accuracy']*100:.2f}%, "
                f"miss={replay.count_miss}."
            ),
            parse_mode="HTML",
        )
        return

    async with get_db_session() as session:
        sub_fresh = (await session.execute(
            select(Submission).where(Submission.id == sub.id)
        )).scalar_one_or_none()
        if not sub_fresh or sub_fresh.status != "tracking":
            await message.reply(
                format_error("Сабмишн уже обработан."), parse_mode="HTML",
            )
            return

        # Double-payout backstop (mirror of the auto-checker): never credit a
        # bounty this user already has approved (duplicate tracking row / a
        # concurrent auto-checker approval).
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
            await message.reply(
                format_error("Этот баунти у вас уже зачтён."), parse_mode="HTML",
            )
            return

        sub_fresh.accuracy    = round(score_dict["accuracy"] * 100, 2)
        sub_fresh.max_combo   = replay.max_combo
        sub_fresh.misses      = replay.count_miss
        sub_fresh.mods        = ",".join(_replay_mods(replay.mods)) or None
        sub_fresh.n_300       = replay.count_300
        sub_fresh.n_100       = replay.count_100
        sub_fresh.n_50        = replay.count_50
        sub_fresh.ur_est      = ur
        sub_fresh.status      = "approved"
        sub_fresh.result_type = result_type
        sub_fresh.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        is_first = (await session.execute(
            select(Submission).where(
                Submission.bounty_id == sub.bounty_id,
                Submission.status == "approved",
                Submission.id != sub_fresh.id,
            )
        )).first() is None

        u = (await session.execute(
            select(User).where(User.id == user.id)
        )).scalar_one_or_none()

        hp_result = await compute_payout(
            session=session,
            user=u,
            bounty=bounty,
            submission=sub_fresh,
            result_type=result_type,
            is_first_submission=is_first,
            bounty_type=bounty.bounty_type,
        )
        hp_awarded = hp_result["final_hp"]
        sub_fresh.hp_awarded = hp_awarded

        if u:
            # Atomic increment so two concurrent award flows can't clobber
            # each other's HP (lost update — audit #7).
            await session.execute(
                update(User).where(User.id == user.id).values(
                    hps_points=User.hps_points + hp_awarded,
                    bounties_participated=User.bounties_participated + 1,
                ).execution_options(synchronize_session=False)
            )
            if u.first_approved_at is None:
                await session.execute(
                    update(User)
                    .where(User.id == user.id, User.first_approved_at.is_(None))
                    .values(first_approved_at=sub_fresh.reviewed_at or utcnow())
                    .execution_options(synchronize_session=False)
                )
            await session.commit()
            await session.refresh(u)
            u.rank = get_rank_for_hp(u.hps_points or 0)
            await session.commit()
        else:
            await session.commit()

    result_names = {"win": "FC", "condition": "Условие выполнено"}
    vanguard = " 🥇 Первый!" if is_first else ""
    await message.reply(
        format_success(
            f"Баунти «{escape_html(bounty.title)}» зачтено{vanguard}!\n"
            f"UR = {ur:.1f} мс, +{hp_awarded} HP ({result_names.get(result_type, result_type)})."
        ),
        parse_mode="HTML",
    )
