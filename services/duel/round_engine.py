"""Duel round engine — runs an accepted duel to completion over osu! IRC.

A duel plays its auto-built pool in order, one map per round, best-of-N
(casual Bo5 → first to 3, ranked Bo10 → first to 6).  Hardcore scoring: a
player who **fails** the map scores no point that round; among passers the
higher score wins; if both fail the round is void (no point).  When the pool
ends level a sudden-death tiebreak map is pulled.  The result feeds the
single-track TrueSkill update.

One background task per duel (de-duplicated via ``_active``); it is launched
on accept and re-launched by the recovery pass after a restart, resuming from
``Duel.current_round``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select

from db.database import get_db_session
from db.models.duel import Duel
from db.models.duel_round import DuelRound
from db.models.duel_map_pool import DuelMapPool
from db.models.user import User
from services.duel.duel_constants import (
    SCORE_POLL_INTERVAL,
    MAP_READY_COUNTDOWN,
    ROUND_FORFEIT_BUFFER_MIN,
    MAX_MONITOR_HOURS,
    MAX_TIEBREAKERS,
)
from services.duel.match_monitor import find_round_score, extract_score_stats
from services.duel.map_selector import get_map_for_round
from services.duel.rating import update_ratings, rating_to_sr
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger("duel.engine")

_active: set[int] = set()


# ── player context ───────────────────────────────────────────────────────────
class _Player:
    __slots__ = ("user_id", "osu_id", "username", "pp")

    def __init__(self, user: User):
        self.user_id = user.id
        self.osu_id = int(user.osu_user_id or 0)
        self.username = user.osu_username or "?"
        self.pp = float(user.player_pp or 0)


# ── public entry ─────────────────────────────────────────────────────────────
def launch(bot, osu_api, duel_id: int) -> None:
    """Start (or resume) the engine task for a duel, de-duplicated."""
    if duel_id in _active:
        return
    _active.add(duel_id)
    asyncio.create_task(_run_guarded(bot, osu_api, duel_id), name=f"duel_engine_{duel_id}")


async def _run_guarded(bot, osu_api, duel_id: int) -> None:
    try:
        await run_duel(bot, osu_api, duel_id)
    except Exception as e:  # never let a duel task die silently
        logger.error(f"run_duel({duel_id}) crashed: {e}", exc_info=True)
    finally:
        _active.discard(duel_id)


# ── helpers ──────────────────────────────────────────────────────────────────
async def _send(bot, duel: Duel, text: str) -> None:
    try:
        await bot.send_message(
            duel.chat_id, text, parse_mode="HTML",
            message_thread_id=duel.message_thread_id,
        )
    except Exception:
        logger.debug(f"duel {duel.id}: send failed", exc_info=True)


def _map_label(m: Optional[DuelMapPool], beatmap_id: int) -> str:
    if not m:
        return f"map {beatmap_id}"
    return f"{m.artist} - {m.title} [{m.version}]"


def _decide_round(p1_stats: dict, p2_stats: dict) -> Optional[int]:
    """Hardcore rule → 1, 2, or None (void).  Failing the map scores nothing;
    among passers the higher score wins."""
    p1_ok, p2_ok = p1_stats["passed"], p2_stats["passed"]
    if p1_ok and p2_ok:
        return 1 if p1_stats["score"] >= p2_stats["score"] else 2
    if p1_ok:
        return 1
    if p2_ok:
        return 2
    return None


async def _await_round_result(
    osu_api, match_id: int, beatmap_id: int,
    p1_osu: int, p2_osu: int, after: datetime, deadline: datetime,
) -> Optional[tuple[dict, dict]]:
    """Poll the linked match until both players have a completed game on the
    map (returns normalized (p1_stats, p2_stats)), or the deadline passes
    (returns None → void round)."""
    while datetime.now(timezone.utc) < deadline:
        try:
            payload = await osu_api.get_match(int(match_id))
        except Exception as e:
            logger.warning(f"get_match({match_id}) failed: {e}")
            payload = None
        if payload:
            found = find_round_score(payload, beatmap_id, p1_osu, p2_osu, after=after)
            if found:
                return extract_score_stats(found[0]), extract_score_stats(found[1])
        await asyncio.sleep(SCORE_POLL_INTERVAL)
    return None


# ── main loop ────────────────────────────────────────────────────────────────
async def run_duel(bot, osu_api, duel_id: int) -> None:
    # Load context.
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status not in ("accepted", "round_active"):
            return
        p1u = (await session.execute(select(User).where(User.id == duel.player1_user_id))).scalar_one_or_none()
        p2u = (await session.execute(select(User).where(User.id == duel.player2_user_id))).scalar_one_or_none()
        if not p1u or not p2u or not duel.pool_beatmap_ids or not duel.osu_match_id:
            logger.error(f"run_duel({duel_id}): missing players / pool / IRC match — abandoning")
            duel.status = "cancelled"
            await session.commit()
            return
        p1, p2 = _Player(p1u), _Player(p2u)
        pool = [int(x) for x in duel.pool_beatmap_ids.split(",") if x.strip()]
        match_id = int(duel.osu_match_id)
        mode = duel.mode
        win_target = duel.win_target
        start_index = duel.current_round
        t1, t2 = duel.player1_rounds_won, duel.player2_rounds_won
        chat_id, thread_id = duel.chat_id, duel.message_thread_id
        if duel.status != "round_active":
            duel.status = "round_active"
        await session.commit()

    from services.bancho_irc import get_irc_client
    irc = get_irc_client()
    watchdog = datetime.now(timezone.utc) + timedelta(hours=MAX_MONITOR_HOURS)

    played: list[int] = pool[:start_index]
    winner_player: Optional[int] = None  # 1 / 2

    # Walk the pool, then tiebreakers, until someone reaches win_target.
    queue = list(enumerate(pool[start_index:], start=start_index))
    tiebreaks_used = 0
    idx = start_index

    while True:
        if t1 >= win_target or t2 >= win_target:
            winner_player = 1 if t1 >= win_target else 2
            break
        if datetime.now(timezone.utc) > watchdog:
            logger.warning(f"run_duel({duel_id}): watchdog timeout — finishing on current tally")
            break

        if queue:
            round_index, beatmap_id = queue.pop(0)
        else:
            # Pool exhausted. Decide or pull a tiebreak.
            if t1 != t2:
                winner_player = 1 if t1 > t2 else 2
                break
            if tiebreaks_used >= MAX_TIEBREAKERS:
                winner_player = 1 if t1 >= t2 else 2  # last-resort fallback
                break
            tiebreaks_used += 1
            target_sr = rating_to_sr((await _avg_mu(p1.user_id, p2.user_id, mode)))
            tb = await get_map_for_round(target_sr, exclude_ids=played)
            if not tb:
                winner_player = 1 if t1 >= t2 else 2
                break
            round_index = idx
            beatmap_id = tb.beatmap_id
            await _send(bot, await _reload(duel_id),
                        "🎲 <b>Тай-брейк</b> — счёт равный, решающая карта!")

        idx = round_index + 1
        played.append(beatmap_id)

        # Map row (title / sr / length).
        async with get_db_session() as session:
            mrow = (await session.execute(
                select(DuelMapPool).where(DuelMapPool.beatmap_id == beatmap_id)
            )).scalar_one_or_none()
            map_label = _map_label(mrow, beatmap_id)
            star = float(mrow.star_rating) if mrow else 0.0
            length_s = int(mrow.length or 180) if mrow else 180
            beatmapset_id = int(mrow.beatmapset_id) if mrow else None

        round_started = datetime.now(timezone.utc)
        deadline = round_started + timedelta(seconds=length_s) + timedelta(minutes=ROUND_FORFEIT_BUFFER_MIN)

        # Persist round + progress (so recovery resumes here).
        async with get_db_session() as session:
            duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one()
            if duel.status != "round_active":
                return  # cancelled meanwhile
            duel.current_round = round_index
            rnd = DuelRound(
                duel_id=duel_id, round_number=round_index + 1,
                beatmap_id=beatmap_id, beatmapset_id=beatmapset_id,
                beatmap_title=map_label, star_rating=star,
                status="playing", started_at=round_started, forfeit_at=deadline,
            )
            session.add(rnd)
            await session.commit()

        await _send(bot, await _reload(duel_id),
                    f"🎵 <b>Раунд {round_index + 1}</b> — <code>{escape_html(map_label)}</code> "
                    f"({star:.2f}★)\nСчёт: <b>{p1.username} {t1} : {t2} {p2.username}</b>")

        # Push the map to the room and wait for the result.
        try:
            await _set_map(irc, match_id, beatmap_id)
        except Exception as e:
            logger.error(f"run_duel({duel_id}): set_map_and_start failed: {e}", exc_info=True)

        result = await _await_round_result(
            osu_api, match_id, beatmap_id, p1.osu_id, p2.osu_id, round_started, deadline,
        )

        # Score the round.
        if result is None:
            point = None
            p1_stats = p2_stats = None
            status = "forfeit"
        else:
            p1_stats, p2_stats = result
            point = _decide_round(p1_stats, p2_stats)
            status = "completed" if point is not None else "void"

        if point == 1:
            t1 += 1
        elif point == 2:
            t2 += 1

        await _persist_round_result(duel_id, round_index + 1, point, status,
                                    p1_stats, p2_stats, t1, t2)
        await _send(bot, await _reload(duel_id), _round_result_text(p1, p2, point, p1_stats, p2_stats, t1, t2))

    await _finish(bot, osu_api, duel_id, winner_player, irc, match_id)


async def _set_map(irc, match_id: int, beatmap_id: int) -> None:
    from services.duel.irc_room import set_map_and_start
    await set_map_and_start(irc, int(match_id), int(beatmap_id), countdown=MAP_READY_COUNTDOWN)


async def _reload(duel_id: int) -> Duel:
    async with get_db_session() as session:
        return (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one()


async def _avg_mu(p1_id: int, p2_id: int, mode: str) -> float:
    from db.models.duel_rating import DuelRating
    async with get_db_session() as session:
        rows = (await session.execute(
            select(DuelRating.mu).where(
                DuelRating.user_id.in_([p1_id, p2_id]), DuelRating.mode == mode,
            )
        )).scalars().all()
    return sum(rows) / len(rows) if rows else 1500.0


async def _persist_round_result(duel_id, round_number, point, status,
                                p1_stats, p2_stats, t1, t2) -> None:
    async with get_db_session() as session:
        rnd = (await session.execute(
            select(DuelRound).where(
                DuelRound.duel_id == duel_id, DuelRound.round_number == round_number,
            )
        )).scalar_one_or_none()
        if rnd:
            rnd.status = status
            rnd.winner_player = point
            rnd.completed_at = datetime.now(timezone.utc)
            if p1_stats:
                rnd.player1_score = p1_stats["score"]
                rnd.player1_accuracy = p1_stats["accuracy"]
                rnd.player1_combo = p1_stats["combo"]
                rnd.player1_misses = p1_stats["misses"]
                rnd.player1_passed = p1_stats["passed"]
            if p2_stats:
                rnd.player2_score = p2_stats["score"]
                rnd.player2_accuracy = p2_stats["accuracy"]
                rnd.player2_combo = p2_stats["combo"]
                rnd.player2_misses = p2_stats["misses"]
                rnd.player2_passed = p2_stats["passed"]
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one()
        duel.player1_rounds_won = t1
        duel.player2_rounds_won = t2
        duel.current_round = round_number  # next map index
        await session.commit()


def _round_result_text(p1, p2, point, p1_stats, p2_stats, t1, t2) -> str:
    if point is None and p1_stats is None:
        body = "⏱ Раунд не сыгран (форфейт) — очко никому."
    elif point is None:
        body = "💀 Оба не прошли карту — раунд пустой, очко никому."
    else:
        win = p1.username if point == 1 else p2.username
        body = f"🏆 Раунд за <b>{escape_html(win)}</b>."
    if p1_stats and p2_stats:
        def line(name, s):
            mark = "✅" if s["passed"] else "💀"
            return (f"{mark} <b>{escape_html(name)}</b>: {s['score']:,} "
                    f"({s['accuracy']:.2f}%, {s['combo']}x, {s['misses']}✗)")
        body += f"\n{line(p1.username, p1_stats)}\n{line(p2.username, p2_stats)}"
    return f"{body}\n\nСчёт: <b>{p1.username} {t1} : {t2} {p2.username}</b>"


# ── finish ───────────────────────────────────────────────────────────────────
async def _finish(bot, osu_api, duel_id: int, winner_player: Optional[int],
                  irc, match_id: int) -> None:
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status not in ("round_active", "accepted"):
            return
        p1u = (await session.execute(select(User).where(User.id == duel.player1_user_id))).scalar_one()
        p2u = (await session.execute(select(User).where(User.id == duel.player2_user_id))).scalar_one()
        p1, p2 = _Player(p1u), _Player(p2u)
        t1, t2 = duel.player1_rounds_won, duel.player2_rounds_won
        mode = duel.mode

    if winner_player is None:
        winner_player = 1 if t1 >= t2 else 2
    winner, loser = (p1, p2) if winner_player == 1 else (p2, p1)

    w_rating, l_rating, w_old, w_new, l_old, l_new = await update_ratings(
        winner.user_id, loser.user_id, mode,
        winner_pp=winner.pp, loser_pp=loser.pp,
    )

    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one()
        duel.status = "completed"
        duel.winner_user_id = winner.user_id
        duel.completed_at = datetime.now(timezone.utc)
        chat_id = duel.chat_id
        thread_id = duel.message_thread_id
        await session.commit()

    text = _finish_text(winner, loser, t1, t2, mode, w_rating, l_rating)
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML",
                               message_thread_id=thread_id)
    except Exception:
        logger.debug(f"duel {duel_id}: finish send failed", exc_info=True)

    # Ranked division changes → promotion/relegation card to the duel chat and
    # the configured DUEL-notify chat (setduelnotifychat).
    if mode == "ranked":
        try:
            from services.duel.division_notify import notify_division_change
            if w_old != w_new:
                await notify_division_change(
                    bot, winner.user_id, w_old, w_new, chat_id, thread_id,
                    duel_points=w_rating.conservative, mode=mode,
                )
            if l_old != l_new:
                await notify_division_change(
                    bot, loser.user_id, l_old, l_new, chat_id, thread_id,
                    duel_points=l_rating.conservative, mode=mode,
                )
        except Exception:
            logger.debug(f"duel {duel_id}: division notify failed", exc_info=True)

    # Close the IRC room.
    try:
        from services.duel.irc_room import close_room
        if irc and getattr(irc, "connected", False):
            await close_room(irc, int(match_id))
    except Exception as e:
        logger.warning(f"_finish({duel_id}): close_room failed: {e}")


def _finish_text(winner, loser, t1, t2, mode, w_rating, l_rating) -> str:
    return (
        f"🏁 <b>ДУЭЛЬ ОКОНЧЕНА</b> ({mode.upper()})\n\n"
        f"🥇 Победитель: <b>{escape_html(winner.username)}</b>\n"
        f"Счёт: <b>{t1} : {t2}</b>\n\n"
        f"<b>{escape_html(winner.username)}</b>: μ {w_rating.mu:.0f} (σ {w_rating.sigma:.0f})\n"
        f"<b>{escape_html(loser.username)}</b>: μ {l_rating.mu:.0f} (σ {l_rating.sigma:.0f})"
    )
